# Firmware

A protocol is firmware, not silicon. The engines decide what happens and the
pin cells decide when; a protocol is a program that schedules pin changes on the
shared timebase. The `protocol_emulator.v3.firmware` package generates those
programs as assembly source for `protocol_emulator.v3.isa.assemble`.

## Program registry

`firmware.PROGRAMS` maps a short name to its generator, so a tool can list the
available firmware without importing each module. Every generator takes a pin
and a rate and returns assembly source. All timing comes from
`protocol_emulator.v3.timing`, which derives each constant from the 48 MHz
clock and refuses a rate the clock cannot represent, so a generator either
returns a program correct for the requested rate or raises `ValueError`.

| Name | Generator | Role |
|---|---|---|
| `uart_tx` | `uart_tx(pin, baud)` | 8N1 UART transmitter on one pin |
| `uart_rx` | `uart_rx(pin, baud)` | 8N1 UART receiver on one pin |
| `spi_controller` | `spi_controller(sclk, cs, mosi, miso, mode)` | SPI controller driving the clock and chip select |
| `spi_target` | `spi_target(sclk, cs, mosi, miso, mode)` | SPI target reacting to the controller's clock |

## UART

Both UART programs use 8N1 framing on a single protocol pin: the line idles
high, each frame is one low start bit, eight data bits least significant first,
and one high stop bit. `pin` is any protocol pin `0`–`7`; `baud` is any rate the
clock can represent within the default rounding tolerance.

### Transmit

`uart_tx` sends one byte per host transmit-queue entry. The host pushes bytes
with `TXPUSH` and the program blocks on `PULL` until one is available, so the
host must keep the queue fed. Each frame re-anchors the engine cursor to the
current time and schedules every edge a few ticks ahead of it, so the start bit
lands in the future even when the queue byte arrived late. The pin cell's single
pending slot throttles the loop to the bit rate: the `OUT` at the head of each
bit waits until the previous bit's commit has fired, so the frame needs no
synchronization instruction.

The data bit is chosen by a branch, because `OUT` takes a literal level. The two
branches are not the same length — ten clocks for a one bit, nine for a zero —
and on a design that timed its output by counting instructions that asymmetry
would jitter the line. Here it cannot: both branches advance the cursor by
exactly one bit period and every edge is scheduled at the cursor, so the edge
positions depend only on the cursor and never on how many instructions a branch
took to reach them. The per-bit work is ten clocks against forty-eight at
1 Mbaud, so the loop keeps up to the fastest rate the framing allows.

### Receive

`uart_rx` samples one pin and never drives it. It waits for the falling start
edge, reads the hardware edge timestamp, and sets the cursor to one and a half
bit periods after it, placing the first sample at the centre of the first data
bit. It then samples the eight data bits at one-bit-period steps and the stop
bit half a period later. Because every sample time is measured from the edge
timestamp rather than by counting instructions, the fixed pad-to-engine input
latency cancels: the level the engine reads is delayed by the same amount as the
timestamp it measured from, so the sample still lands mid-bit.

Each received frame is pushed with `RX` as a `(value, meta)` record, read back
with `RXPOP`:

- `value` is the received byte, the eight data bits with the first-received
  (least significant) bit in bit 0.
- `meta` flags framing. Bit 0 is the framing-error flag: `0` when the stop bit
  was high (a clean frame) and `1` when the stop bit was low (a framing error,
  and the byte in `value` is suspect). Bits 7:1 are reserved and zero.

The generated programs for pin 0 at 115200 baud are checked in at
`examples/v3/uart_tx_115200.asm` and `examples/v3/uart_rx_115200.asm`.

## SPI

The SPI programs implement a controller and a target on four separate protocol
pins: `sclk`, `cs`, `mosi` and `miso`. Each pin is any protocol pin `0`–`7`,
the four must be distinct, and `mode` is `0`–`3`. Transfers are eight bits, most
significant bit first, chip select is active low, and the clock runs at 1 MHz.
The half period is taken from `protocol_emulator.v3.timing.SPI_HALF`; no cycle
count is written as a literal. One byte is transferred in each direction per
host transmit-queue entry, and the byte received from the wire is pushed with
`RX`.

### Modes

`mode` encodes clock polarity and phase. CPOL is `mode >> 1` and is the idle
level of `sclk`; CPHA is `mode & 1` and selects the sampling edge:

- CPHA 0 samples on the leading edge of each clock period and shifts on the
  trailing edge. The first data bit of a byte is presented before the first
  leading edge, at chip-select assertion.
- CPHA 1 shifts on the leading edge and samples on the trailing edge.

Both ends change their outgoing data on the shifting edge and latch the incoming
line on the sampling edge, so `mosi` and `miso` move and are sampled together.

### Controller

`spi_controller` drives `sclk`, `cs` and `mosi` and samples `miso`. It pulls one
byte with `PULL`, re-anchors the engine cursor to the current time, and schedules
every edge a few ticks ahead of the cursor; the cursor advances by one
`SPI_HALF` each half period, so the waveform is a clean 1 MHz clock no matter how
many instructions a data-bit branch took. As in the UART transmitter the data
bit is chosen by a branch — `OUT` takes a literal level — and the branch
asymmetry cannot jitter the clock because every edge is scheduled at the cursor.
The controller samples `miso` late within the sampling half period, just before
it issues the next shifting edge, so a target reacting to the previous edge has a
full half period to present its bit through the input and output latency.

### Target

`spi_target` drives only `miso` and samples the other three pins; it has no
clock of its own and so needs no bit-period constant. It waits for chip select
to fall and then follows the controller's clock edge by edge with `WAITEDGE`:
on each sampling edge it shifts `mosi` into the input shift register, and on each
shifting edge it presents the next `miso` bit a few ticks later. Because it only
ever acts relative to a hardware edge timestamp, the fixed pad-to-engine input
latency is absorbed — the controller's half period is far longer than the
synchroniser and filter delay, so the bit the target presents on one edge is
stable well before the controller samples it on the next. Each `WAITEDGE` is
armed with a far horizon and re-arms from the start on a timeout, so a target
left without a transfer waits rather than advancing on stale state.

### Receive record encoding

Each received byte is pushed with `RX` as a `(value, meta)` record, read back
with `RXPOP`:

- `value` is the eight received bits with the first-received (most significant)
  bit in bit 7.
- `meta` is always `0`. SPI has no framing or parity, so there is no error to
  flag; the meta byte is reserved and zero.

The generated controller and target for mode 0 on pins `sclk=0`, `cs=1`,
`mosi=2`, `miso=3` are checked in at `examples/v3/spi_controller_mode0.asm` and
`examples/v3/spi_target_mode0.asm`.
