# Project history

V3 replaces the V1 and V2 designs. Their sources are not in this tree. The tags below still
point at them.

## V1

Tag `v1.0.0` (23 September 2026) is the V1 electrical signoff.

V1 is a single programmable timing engine with SPI host control, aimed at the IHP CMOS5L
Tiny Tapeout flow. The instruction set, host contract and cycle model were developed through
September 2026 and closed by the electrical signoff on 23 September 2026. Recover that tree
with `git checkout v1.0.0`.

## V2

Tag `v2` (18 October 2026) is the V2 release candidate, package version `2.0.0rc1`.

V2 runs two independent engines at 25 MHz. Each engine holds 64 instruction words of 24 bits
in flip-flops, with a 16-byte transmit queue and a 16-record receive queue. A 32-record
capture unit records pin activity without driving the pins. Firmware implements UART transmit
and receive, SPI modes 0-3 as controller or target, and I2C at 100 kHz and 400 kHz as
controller or target. The physical target is 6x4 tiles, a 40 ns clock, 1.5 ns maximum
transition and 0.25 ns clock uncertainty.

The selected physical checkpoint is a local Tiny Tapeout recovery, chosen on 18 October 2026
after the hosted candidate run reached its six-hour limit. No silicon or FPGA operation was
claimed for that candidate. The layout notes, evidence manifests and V2 documentation are in
the `v2` tag (`docs/`, `evidence/`, `src/v2_*.v`). Recover that tree with `git checkout v2`.

## Why the architecture changed

Three limits in V2 set the shape of V3.

- Program memory built from flip-flops capped each engine at 64 words and dominated the
  standard-cell area.
- Time was an instruction delay. An engine that was late had no separate deadline, so a pin
  edge and the instruction that requested it were the same event.
- 25 MHz left little margin for faster waveforms, and a private capture timestamp could not
  be lined up with the engines' notion of time.

V3 is a clean break, not a compatible revision. Engines decide what happens and pin cells
decide when. A shared 24-bit timebase runs at 48 MHz. Each of the eight protocol pins holds
one pending commit and applies it on its due tick. Each engine has a 256-word SRAM program
memory and a 32-bit instruction word. V1 and V2 compatibility, RTL and test suites are not
part of this tree.

## V3 in this tree

The cycle-accurate Python model, the RTL blocks, their induction proofs and the top-level
integration are in this tree. Firmware protocol peers, the physical flow and the FPGA
prototype are not. The architecture is described in [the V3 architecture](design/v3-architecture.md).
