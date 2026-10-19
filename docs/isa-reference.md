# V2 ISA reference

Instructions are 24 bits, with opcode in bits 23:20 and reserved bits zero.
Each ASIC engine has 64 words, addressed 0–63; two 16-bit registers R0/R1;
a 16-bit output shifter (OSR); an 8-bit input shifter (ISR); 16 TX bytes;
16 RX records (byte plus metadata); and two return-stack entries.
Pins are 0–7 and register selectors 0–1. One system cycle is 40 ns.

## Base instructions

| Opcode | Instruction | Operand encoding | Effect |
| --- | --- | --- | --- |
| 0 | NOP | zero | Advance |
| 1 | SET mask, value | mask[15:8], value[7:0] | Update masked output bits |
| 2 | OE mask, value | mask[15:8], value[7:0] | Update masked enables, subject to ownership/open-drain gating |
| 3 | LI r, immediate | r[16], immediate[15:0] | Load register |
| 4 | PULL r | r[16] | Wait for TX byte, zero-extend into register |
| 5 | MOVOS r | r[16] | Copy register to OSR |
| 6 | OUT pin, direction | pin[2:0], direction[3] | Shift OSR; 0=LSB/right, 1=MSB/left, zero fill |
| 7 | SAMPLE r, mask | r[16], mask[7:0] | Zero-extend masked input |
| 8 | DELAY cycles | cycles[15:0], nonzero | Occupy exactly this many cycles including issue |
| 9 | WAIT pin, level, timeout | pin[2:0], level[3], timeout[19:4], nonzero | Wait; fatal on timeout |
| A | DJNZ r, target | r[16], target[6:0] | Decrement modulo 65536; branch unless zero |
| B | JMP target | target[6:0] | Branch |
| C | HALT | zero | Stop and release outputs |

V1 uses six target bits. V2's seventh bit supports the 128-word Python feasibility
option; the 64-word ASIC faults on an out-of-range taken target. Assemble with
`capacity=64` and use the V2 loader to reject such targets before START.

## Extensions

The architecture's extension table
lists every opcode D subopcode (IN, RX, CLEARIS, BRPIN, BREQ, BRC, DRIVE, WAITFOR,
MARK, WAITEVENT, MOVIS, OUT8, PULLNB, CLEAREVENT, FAIL, BRDIFF), the operand widths
packed from bit zero upward, and each effect. Bits 19:16 select that subopcode.

Opcode E uses the same subopcode bits: CALL=0 with target[6:0]; RET=1 and
UNLINK=2 with zero operands; WAITBR=3 with target[6:0], pin[9:7], level[10].
Other opcode E encodings and all opcode F encodings are illegal. CALL stores
return PC in the two-entry stack; RET pops it; UNLINK empties it. Overflow,
underflow, or a CALL without a valid next return address faults.

BRC conditions are 0=TX empty, 1=RX full, 2=last wait timed out,
3=last wait interrupted, 4=watch event 0, 5=watch event 1, 6=PULLNB empty.
Other conditions are illegal. RX appends ISR and immediate metadata without
clearing ISR. BREQ compares the entire selected 16-bit register with the byte
operand. OUT8 shifts the high bit of the low OSR byte, then zero-fills.

## Timing and ordering

Ordinary instructions, branches, and nonblocking operations take one cycle.
PULL stalls until TX was nonempty at the edge; a simultaneous push into an
empty queue is visible on the following edge. PULLNB retires in one cycle and
leaves the register unchanged if empty. A full RX queue rejects a new record,
preserves existing records, sets overflow and halts the affected engine.

WAIT observes synchronized inputs on issue and each stalled cycle. An n-cycle
timeout permits n observations; a match wins over timeout. WAITFOR, WAITEVENT
and WAITBR use R1 without modifying it; zero is illegal. Watch abort sets
`interrupted` even when the pin matches simultaneously. WAITBR branches on
interrupt/timeout and otherwise advances. Timeout/interruption flags change
when a wait retires. Events latch before observation; CLEAREVENT wins over a
simultaneous set of the same event. Input synchronization and optional filtering
add the latency documented in the architecture.

## Errors, memory and compatibility

Unwritten fetch, reserved encodings, invalid operands, address fallthrough,
stack faults, fatal wait timeouts, and FAIL stop execution and release outputs.
FAIL retains its immediate detail code alongside the sticky firmware error.
A fatal engine fault stops that engine; malformed shared host traffic stops
both. Rejected busy operations and TX-capacity requests preserve active state.
The host reports running at status bit 0 and error flags shifted left by one.

External reset invalidates program storage without initializing its data.
State reset preserves program validity; powered disable is not power-loss
retention. Follow the [restart sequence](reset-and-power.md) after state loss.
V1 opcodes 0–C retain their encodings and cycle behavior within V1 address limits,
but host clients require version selection.
