# V1 executable contract

ISA version 1 and host interface version 1 target one 25 MHz engine with 64
24-bit program words, two 16-bit registers, a 16-bit output shifter, and a
four-byte TX FIFO. UART is firmware. The 6x4 CMOS5L implementation is the release
gate. A second engine, receive FIFO, on-chip trace, and other protocols follow V1.

## Execution

Instructions retire on rising system-clock edges. Ordinary instructions take
one cycle. `DELAY n` takes exactly n cycles including its issue cycle; n must be
1..65535. `PULL` retires only when the FIFO was nonempty at the start of the
cycle. A simultaneous push into an empty FIFO becomes visible on the next
cycle. `WAIT` tests the synchronized input on its issue cycle and each stalled
cycle; a match wins over timeout. A timeout of n allows exactly n observations.
Timeout is fatal. `DJNZ` decrements modulo 65536, branching unless the result is
zero. Falling through address 63 is fatal; a taken branch there is legal.

Program storage is not reset; a 64-bit validity map is cleared by external
reset. Fetching an unwritten word is fatal. START is valid only while stopped
and with word zero valid; it clears working state/PC/output registers, preserves
FIFO contents and sticky errors, and starts fetching on the following cycle.
STOP releases outputs and stops execution, preserving FIFO and registers.
STATE_RESET clears execution state, FIFO, and sticky errors but retains the
program. External reset also invalidates the program. Disable (`ena=0`) clears
execution state and FIFO, retains program validity and errors, and never resumes
automatically. Control commands take priority over instruction execution.

Inputs pass through two flip-flops; execution observes the previous second
stage at its edge. Async pin changes become visible after 2–3 system periods;
pulses shorter than three periods have no guaranteed observation. Host SPI is
limited to 1 MHz with each high/low interval and CS setup/hold/inactive interval
at least 200 ns. No generated clock is used.

Protocol output enables are masked by reset, selection, and running state.
HALT, STOP, disable, reset, or a fatal error release all eight pins. `OE` may
enable arbitrary pins; external wiring must avoid conflicting push-pull drivers.

## ISA encoding

Bits 23:20 are the opcode; all unlisted operand bits MUST be zero. Pin numbers
are 0..7, register selectors 0..1, and branch targets 0..63. Invalid encodings
are rejected by the assembler and loader and fault if fetched by the engine.

| Opcode | Assembly | Operand |
|---|---|---|
| 0 | NOP | zero |
| 1 | SET mask, value | mask[15:8], value[7:0] (value outside mask ignored) |
| 2 | OE mask, value | same mask/value layout |
| 3 | LI r, immediate | register[16], immediate[15:0] |
| 4 | PULL r | register[16]; zero-extend FIFO byte |
| 5 | MOVOS r | register[16] to output shifter |
| 6 | OUT pin, direction | pin[2:0], direction[3]; 0=LSB/right, 1=MSB/left; zero fill |
| 7 | SAMPLE r, mask | register[16], input mask[7:0]; zero-extend |
| 8 | DELAY cycles | cycles[15:0], nonzero |
| 9 | WAIT pin, level, timeout | pin[2:0], level[3], timeout[19:4], nonzero |
| A | DJNZ r, target | register[16], target[5:0] |
| B | JMP target | target[5:0] |
| C | HALT | zero |
| D–F | reserved | illegal |

Labels are allowed as branch targets. Numbers use decimal or Python-style
hexadecimal/binary prefixes. Comments begin with `#` or `;`. Assembled images
contain three bytes per word, most-significant byte first, at most 192 bytes.

## Host SPI

Dedicated inputs: ui0=SCK, ui1=CS_n, ui2=MOSI; remaining inputs ignored.
Dedicated outputs: uo0=MISO, uo1=running, uo2=any sticky error; others zero.
Protocol pins are uio0..7; UART firmware uses uio0.

Mode 0, MSB first, exactly 40 clocks per CS assertion. Request bytes are
command, address, payload[23:16], payload[15:8], payload[7:0]. Requests commit
on synchronized CS deassertion only. Short/long frames cannot partially write.
MISO is zero outside selection. The response to request N is shifted during
request N+1: command (bit 7 set on rejection), echoed address, 24-bit result.
Send NOP to fetch a response. Reset response is five zero bytes. Hosts serialize
request/NOP pairs. Payload and address must be zero unless specified below.

| Command | Name | Request / result |
|---|---|---|
| 00 | NOP | no operation / zero |
| 01 | WRITE | address 0..63, valid instruction / written word |
| 02 | READ | address 0..63 / word, or reject if unwritten |
| 03 | START | / zero |
| 04 | STOP | / zero |
| 05 | STATE_RESET | / zero |
| 06 | STATUS | / status word |
| 07 | PUSH | payload low byte / FIFO level after push |
| 08 | LEVEL | / FIFO level |
| 09 | VERSION | / 0x010140 (host v1, ISA v1, 64 words) |

STATUS: bit0=running, bit1=halted (the inverse), bit2=FIFO empty, bit3=FIFO
full, bit4=illegal instruction/unwritten fetch, bit5=invalid address or PC
fallthrough, bit6=write/start while running, bit7=invalid command/operands,
bit8=malformed SPI frame, bit9=FIFO overflow, bit10=WAIT timeout, bits18:16=FIFO
level. Remaining bits are reserved and zero; PC is not exported.
READ of unwritten memory returns a rejected response and sets bit4.

Busy writes/starts and FIFO overflow are nonfatal rejections; they do not alter
the program/FIFO or interrupt an active engine. Invalid commands, addresses,
instructions, malformed frames, and WAIT timeout are fatal and stop execution.
All errors are sticky until STATE_RESET or external reset. A PUSH seeing a full
FIFO is rejected even if an instruction pops that cycle. Status/level responses
are snapshots before instruction execution on the request edge.

## UART firmware and evidence

8N1, LSB first: low start, eight data bits, high stop. Bit periods are exactly
25 cycles (1 Mbaud) or 217 cycles (approximately 115207 baud). The unrolled
firmware accounts for OUT, branch, PULL, and MOVOS cycles in delay operands.
It waits for new FIFO data while the line is high. Available data gives exactly
one stop bit between frames. No instruction-memory rewrite is required.

Each release must include independent UART decoding, cycle-level comparison
against the Python model, formal safety results, and the pinned CMOS5L flow's
GDS/precheck/LVS/routed setup-and-hold reports plus representative gate-level
UART tests. Functional netlist simulation is distinct from timing signoff.
