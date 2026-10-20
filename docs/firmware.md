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
