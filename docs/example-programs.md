# Executable example programs

Run from the repository after installing the package:

```sh
python examples/v2/run.py --output build/examples-new
```

Each scenario emits assembly (`.asm`), an assembled raw image (`.bin`), a
40 ns cycle-domain trace (`.trace.json`), and an entry in `results.json` with
word count, image checksum, ownership/open-drain masks, and checked results.
The command exits nonzero on a mismatch. Generation calls the existing firmware
functions and assembles with `capacity=64`; no alternate protocol implementation
is introduced. Reassemble a generated source with
`assemble(Path(path).read_text(), capacity=64)` from `protocol_emulator.v2.isa`.
The [toolchain page](assembler-toolchain.md) shows decoding, image writing and loading.

## Protocol matrix

All protocol pin numbers below are `uio` bits. Dedicated host pins remain
ui0=SCK, ui1=CS_n, ui2=MOSI and uo0=MISO.

| Protocol | Role/rate | Generated program or scenario | Pins and expected transaction | Executable verification |
| --- | --- | --- | --- | --- |
| UART | TX and RX, 115200 baud | `uart_tx_115200`, `uart_rx_115200`, `uart_duplex_115200` | TX=0, RX=1; transmit `00 55 a5 ff`, receive `81 5a 01 fe`; metadata zero | `tests/v2/test_uart.py`, `test_v2_full_duplex_host_budget` |
| UART | TX and RX, 1 Mbaud | `uart_tx_1000000`, `uart_rx_1000000`, `uart_duplex_1000000` | Same four-byte duplex trace | `tests/v2/test_uart.py`, `test_v2_uart_megabaud_buffered_duplex` |
| SPI | Controller, mode 0 | `spi_controller_mode0` | MOSI=0, SCK=1, CS_n=2, MISO=3; transmit a5, receive ff | `tests/v2/test_spi.py`, V2 pin controller suite |
| SPI | Target, mode 0 | `spi_target_mode0` | Same pins; controller sends ff and receives a5 | `tests/v2/test_spi.py`, V2 pin target suite |
| SPI | Controller, mode 1 | `spi_controller_mode1` | Same a5/ff trace | Controller mode parametrization and pin suite |
| SPI | Target, mode 1 | `spi_target_mode1` | Same ff/a5 trace | Target mode parametrization and pin suite |
| SPI | Controller, mode 2 | `spi_controller_mode2` | Same a5/ff trace | Controller mode parametrization and pin suite |
| SPI | Target, mode 2 | `spi_target_mode2` | Same ff/a5 trace | Target mode parametrization and pin suite |
| SPI | Controller, mode 3 | `spi_controller_mode3` | Same a5/ff trace | Controller mode parametrization and pin suite |
| SPI | Target, mode 3 | `spi_target_mode3` | Same ff/a5 trace | Target mode parametrization and pin suite |
| I2C | Controller, 100 kHz | `i2c_controller_100000` | SDA=0, SCL=1; address 42; write 55 a5, repeated START, read 81 5a, STOP | `tests/v2/test_i2c.py`, V2 pin I2C controller suite |
| I2C | Target, 100 kHz | `i2c_target_100000` | Same transaction from external controller | Target write/read tests and pin suite |
| I2C | Controller, 400 kHz | `i2c_controller_400000` | Same combined transaction | Controller timing/combined tests and pin suite |
| I2C | Target, 400 kHz | `i2c_target_400000` | Same combined transaction | Target write/read tests and pin suite |

Source links: [UART tests](https://github.com/mcranny/protocol-emulator-asic/blob/v2-current/tests/v2/test_uart.py),
[SPI tests](https://github.com/mcranny/protocol-emulator-asic/blob/v2-current/tests/v2/test_spi.py),
[I2C tests](https://github.com/mcranny/protocol-emulator-asic/blob/v2-current/tests/v2/test_i2c.py),
[RTL pin scenarios](https://github.com/mcranny/protocol-emulator-asic/blob/v2-current/test/v2/test_pins.py).
The branch links aid navigation; acceptance uses immutable source identities.

## Configuration and limits

UART uses 217 cycles per bit (approximately 115207 baud) or exactly 25 cycles
(1 Mbaud). The examples run TX and RX concurrently on separate engines with
four queued bytes, within each 16-entry queue. They do not simulate host
refill. The RTL 115200-baud budget test services 145 TX and 128 RX bytes at
500 us intervals, with maximum service time 492 us. This is a measured simulation
budget, not a Linux scheduling guarantee. At 1 Mbaud, the 1 MHz SPI host cannot
sustain arbitrary continuous duplex traffic; preloaded/bounded bursts are the
supported example. Overflow and starvation remain meaningful limits.

SPI examples use 1 MHz SCK (25 system clocks per period). Modes 0/1 have idle
SCK low; modes 2/3 high. Even modes sample the leading edge and odd modes the
trailing edge. Controller ownership is 0x07; target ownership is 0x08.
Targets configure watcher 0 `(mask=4, value=4, edges=4)` and abort mask 1 for
CS release. The model driver includes a three-cycle input pipeline for targets.
The existing controller tests additionally cover 100 kHz.

I2C examples configure owner and open-drain masks 0x03. Pull-ups resolve released
SDA/SCL high in the digital peer. Targets use START/STOP watchers `(3,2,1)` and
`(3,3,1)` with abort mask 3. The generated combined controller occupies all 64
words; the target uses 62. They use the bypass input path; filtered controller
firmware must be generated with `filtered=True` and mask 3 configured consistently.
Existing pin tests cover the filtered path. Bus stretching and instruction
boundaries may reduce transaction throughput below the nominal clock ceiling.
These digital examples do not establish analog rise times or hardware operation.
