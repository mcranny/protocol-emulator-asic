# Capture candidate

`protocol_capture_v2` and `protocol_emulator.v2.capture.Capture` define the same
bounded capture behavior. The block is connected to the V2 integration top
and host API. Its combined area and timing are not part of the earlier passing
local route experiment. The debugging vertical slice is not yet complete.

## Sampling and triggers

Capture samples immediately before engine execution on a system-clock edge.
Outputs and markers produced by that execution appear at the next sample.
ARM starts a timestamp counter at zero. It replaces any preceding capture and
takes an initial sample; immediate, already-asserted marker and already-asserted
error triggers may fire on that sample. A pin trigger requires a later change
in a selected input bit. Capture does not reconstruct asynchronous input phase.

Trigger modes are immediate, masked input transition, selected firmware marker
and error. Input flags 0/1 select engine markers and flag 2 selects error.
Flag 3 is reserved for the capture-generated trigger pulse; other flags remain
generic sampled event bits. Integration uses flags 4/5 for engine running state
and leaves flags 6/7 zero. The marker mask selects flags 0/1. The error trigger
uses flag 2 regardless of mask.

The first record contains the complete state on the trigger edge. Before that
edge no records are kept. Afterwards, each change in input state, output value,
output enable or flags produces one record. Each asserted firmware-marker
cycle also produces a record, preserving consecutive identical markers.
Simultaneous changes share one
timestamp. The trigger pulse clears on the following sample, producing a record
even when the other signals remain constant.

## Records and completion

Each of the 32 records is exactly 64 bits:

| Bits | Value |
|---|---|
| 63:32 | Cycle timestamp since ARM |
| 31:24 | Observed input values |
| 23:16 | Output values |
| 15:8 | Output enables |
| 7:0 | Event flags |

Capture freezes immediately after writing record 32 or sampling timestamp
`0xffffffff`, and reports truncation. The maximum 25 MHz span is approximately
171.799 seconds; the counter never wraps. STOP freezes before taking another
sample. RESET clears metadata and record validity. Read addresses at or above
the record count return zero, so unwritten storage is never exposed.
Global disable freezes the records with reason `disabled`; it does not claim
normal completion. Rearming requires the device to be enabled.

Completion requires a trigger followed by an explicit STOP without truncation.
The model rejects ordinary export of armed, untriggered or truncated captures.
An explicitly requested diagnostic export can include incomplete records and
their reason. VCD is a waveform view with 40 ns per timestamp tick; before a
delayed trigger its values are unknown. It is not a replay source. Scenario
firmware/configuration/stimulus provenance and replay remain separate work.

## Host access

The Python API provides `capabilities()`, `capture_arm(mode, mask)`,
`capture_stop()` and `capture_read(allow_incomplete=False)`. Readout requires
frozen storage and reconstructs each record from three response payloads.
It returns the same versioned capture object as the model. Record reads are
nondestructive; incomplete diagnostic reads require explicit opt-in.

| Command | Payload / result |
|---|---|
| 0B | Features low 16 bits, capture depth high byte; current value `0x20001f` |
| 30 | ARM: mode bits 1:0 (immediate/pin/marker/error), mask bits 15:8 |
| 31 | STOP capture |
| 32 | Status: armed/triggered/truncated bits 0/1/2, reason bits 5:3, count bits 11:6 |
| 33 | Address 0/1: end timestamp low 24/high 8; address 2/3: trigger timestamp |
| 35/36/37 | Record address 0..31: low 24, middle 24, high 16 bits |

Status reasons 0..7 are reset, armed, capturing, stopped, untriggered, capacity,
timestamp and disabled. Feature bits 0..4 indicate capture, two engines,
open-drain enforcement, input watchers and packed/pipelined FIFO transport.
Reserved request bits must be zero. Timestamp and record reads during capture
are rejected without stopping engines or modifying capture storage.

## Validation

`make v2-capture-test` runs lint, edge-by-edge differential comparison including
every stored record, and induction using the production 32-bit timestamp.
Differential simulation uses an 8-bit counter to exercise exhaustion directly.
It covers resets, rearming, all triggers, full capacity, frozen reads and random
state changes. Python tests additionally check export refusal, packed records,
VCD values, marker ordering and observation without changing engine waveforms.

Formal properties cover record-count bounds, no timestamp wrap while armed,
truncation freeze and metadata stability. Pin-driven integration tests cover
host readout, marker/error triggers, full-buffer freeze with ongoing execution
and captured/uncaptured waveform equivalence. Complete trigger/fault
demonstrations and combined physical acceptance are still required.
