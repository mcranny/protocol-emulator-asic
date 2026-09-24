# Phase 2 implementation sequence

Review date: 2026-09-19. This is a proposed implementation sequence, not a
claim that V1 has met its release gates. Measured evidence and remaining
signoff issues are in the [validation record](validation.md).

## Current capability

The V1 functional implementation is merged. The project contains one programmable timing
engine, 64 x 24-bit instruction memory, two working registers, an output
shifter, a four-byte TX FIFO, an SPI host path, Python tools and reference
model, and firmware-driven UART 8N1 transmission. Tests cover programming
readback, instruction behavior, faults, stop/reset/disable safety, and UART
transmission with concurrent SPI access. No FPGA or silicon operation has
been demonstrated.

At 25 MHz, ordinary instructions take 40 ns. UART firmware uses 25 words
(39.1% of program capacity). Its 25-cycle bit interval gives exactly 1 Mbaud;
217 cycles gives approximately 115207.37 baud (+0.0064% versus 115200).
Back-to-back 8N1 payload rates are therefore 100000 and approximately 11520.74
bytes/s while bytes are available. The retained RTL tests verify both bit
intervals and exact frame spacing for 00, 55, A5, and FF.

The host interface is limited to 1 MHz SPI. The current API uses one 40-bit
request plus a 40-bit response-retrieval frame per byte pushed: at least
80 us/byte, or at most 12500 bytes/s before CS gaps and software overhead.
Thus 1 Mbaud is a queued-burst capability, not sustained host-to-UART
throughput. Even pipelining one byte per 40-bit frame would peak at 25000
bytes/s before gaps. Continuous 115200-baud service has little margin and
has not been demonstrated on a hardware host. Four queued bytes represent
40 us of 1-Mbaud output or 347.2 us at the slower rate. A larger FIFO absorbs
host jitter but cannot correct a sustained bandwidth deficit.

## Gate 0: close V1 before expanding functionality

The primitive-model and release-check fixes are merged. Local routed
electrical checks pass; same-revision full physical acceptance and release
publication are the required exit gates.
Use the [validation record](validation.md) as the single source of current
measurements and blockers; do not add Phase 2 features to the closure PR.

Exit: all mandatory V1 gates pass from a clean checkout, with no unresolved
electrical violations. Do not describe a development merge as a V1 release.

## Phase 2A: bidirectional single-engine UART

Freeze receive semantics before RTL changes: input-shifter/accumulation
support, sampling phase, start validation, stop/framing errors, break,
timeouts, RX FIFO overflow, host readback, and reset behavior. Extend the ISA
and host interface only where necessary, with explicit version compatibility.
Keep UART reception in engine firmware, not a dedicated fixed UART decoder.

Deliverables: RX firmware at both rates, receive-data path and bounded FIFO,
assembler/model/loader support, independent serial stimulus, and byte-exact
host readback. Sweep input phase and document tolerated baud mismatch. Test
back-to-back traffic, malformed frames, starvation/overflow, and reset/stop
during reception. A single engine need not claim simultaneous TX/RX yet.

Exit: every-cycle differential checks and formal FIFO/safety properties pass;
routed area/timing, electrical rules, precheck, and gate-level RX tests pass.

## Phase 2B: host throughput and concurrent engines

Set a sustained-throughput target and budget first. Consider packed-byte FIFO
commands, batched response handling, and a carefully verified higher host
clock rate. Size FIFOs against measured host service latency. Do not merely
raise the SPI limit without revisiting synchronizers and phase tests.

Then introduce a second engine for independent TX/RX. Specify instruction
memory ownership, command routing, per-engine state/FIFOs, start/stop events,
and exclusive pin ownership before implementation. Prove that no combination
of commands or faults can cause conflicting output drive. Preserve V1 timing
for programs intended to remain compatible.

Exit: simultaneous TX/RX under worst-case host load, byte-exact scoreboards,
no unintended timing interference, and renewed 6x4 physical closure. Local
27.0510% cell utilization is encouraging, not proof that a second engine fits.

## Phase 2C: deterministic debugging and protocol expansion

Add bounded timestamped event capture, trigger/overflow semantics, and host
readout before replay. Store test seeds and programmable fault schedules so
pin traces can be reproduced. Keep fault injection constrained by output
ownership and reset safety.

Then implement emulated SPI and I2C one role at a time as engine firmware,
distinct from the dedicated host SPI interface. I2C requires explicit
open-drain behavior, clock stretching, and contention/arbitration semantics;
SPI requires documented edge modes and timing limits. Extend the model and
independent protocol checkers with each role. Re-run physical gates after
each hardware capability rather than waiting for all protocols.

SRAM, larger memories, optional protocols, and FPGA bring-up remain separately
gated decisions. Prioritize deterministic behavior and observable failures
over protocol count. Each capability should be a small, neutral PR with its
own reproducible acceptance evidence.
