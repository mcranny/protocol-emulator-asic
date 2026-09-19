## How it works

One programmable engine executes up to 64 24-bit instructions. It can write and
enable pins, shift bits, sample inputs, wait, delay, and branch. A dedicated SPI
host port loads programs and supplies a four-byte TX FIFO. The V1 UART program
implements 8N1 transmission at 1 Mbaud or approximately 115200 baud with a
25 MHz system clock. Programs use all eight bidirectional pins as needed.

Outputs are released on reset, disable, stop, halt, or fatal error. Program
memory must be loaded after external reset. Instruction timing and the complete
host protocol are documented in v1-contract.md.

## How to test

Connect a mode-0 SPI host to ui0 (SCK), ui1 (CS_n), ui2 (MOSI), and uo0 (MISO).
Use at most 1 MHz SCK and the timing constraints in the host specification.
uo1 indicates running; uo2 indicates a sticky error.

Load and verify a UART program, enqueue bytes, and start execution. UART TX
appears on uio0. Use a pull-up if the receiving device requires idle high while
the engine is stopped and its output is released.

The simulation suite drives the same top-level pins and independently decodes
UART output. Check validation.md for the actual physical-flow status.

## External hardware

A compatible SPI host and UART receiver or logic analyzer are sufficient for
a future physical test. Check the board and pad voltage requirements before
connecting external equipment. No physical demonstration has been completed.
