# UART fault scenarios and replay

The V2 simulation scenario runner demonstrates a scheduled malformed stop bit,
independent UART reception, triggered capture, JSON/VCD export and both replay
forms. It exercises the provisional ISA and has pin-driven RTL checks. Combined
physical and routed-netlist acceptance remain pending.

From the repository root, using the installed Python test environment:

```sh
PYTHONPATH=host python -m protocol_emulator.v2.scenario uart-demo build/demo/fault
PYTHONPATH=host python -m protocol_emulator.v2.scenario replay build/demo/fault.json
PYTHONPATH=host python -m protocol_emulator.v2.scenario playback build/demo/fault.json > build/demo/playback.asm
PYTHONPATH=host python -m protocol_emulator.v2.scenario uart-demo build/demo/fixed --good-stop
```

The default bit period is 217 clocks; `--period 25` selects the bounded 1-Mbaud
example. Engine 0 sends byte `a5`, marking the cycle immediately before its
stop instruction. The fault drives that stop bit low. An independent decoder
reports `framing_error: true`; `--good-stop` changes the firmware to drive a
valid stop bit, and decoding passes. Engine 1 concurrently receives a byte from
an independent external source whose edges are offset 13 ns from clock edges.
The seed selects that source byte; the exported stimulus is authoritative.

Capture triggers on the marker. Its first record is the state immediately
before the stop instruction; the following record shows the resulting stop
level and marker deassertion. JSON carries the complete bounded regression.
VCD shows synchronized capture observations, with unknown state before the
trigger; it does not contain the original asynchronous stimulus waveform.

## Scenario contract

Schema `protocol-emulator-scenario`, version 1, includes:

- Host and ISA versions, 25 MHz clock, 64-word capacity and seed.
- Two firmware images with SHA-256 over their big-endian 24-bit words.
- Ownership/open-drain masks, atomic start mask and initial TX queues.
- Reset engine state, released outputs and settled input synchronizers.
- Original external input transitions with integer nanosecond timestamps.
- The firmware fault's engine, execution cycle and duration.
- Capture configuration and completeness, expected decoded frames, RX records,
  engine errors, output events and all captured cycle events.

Cycle zero is the first execution edge after atomic START. Capture ARM is at
that same execution edge in the reference scenario. Stimulus exactly on an
edge is applied before input sampling. Two synchronizer stages separate raw
input sampling from execution observation. This deterministic digital ordering
does not model metastability or recover asynchronous phase from captured data.

The current runner accepts one bounded window of at most one million cycles,
preloaded TX queues, and external changes on unowned pins. Owned pins feed their
driven values back through the input synchronizers; released pins observe the
external idle level. The decoder observes one owned UART output. Mid-run host
servicing, input watchers, SPI/I2C scenarios and board electrical models are
not yet part of this scenario version's supported runner.

Replay validates versions, firmware hashes, pin ownership, timestamps and
capture completeness before executing. It reuses the original external
stimulus and compares decoded results and cycle events exactly. The first
differing result category includes expected and actual data in its error.
Fault metadata must match the decoded framing-error timing and ownership.
Incomplete capture cannot be written as a regression source.

The `playback` command first verifies simulation replay, then compiles the
aggregate output waveform into ordinary engine firmware. It reproduces output
values/enables at the recorded execution-cycle spacing, including the final
release; it does not drive recorded inputs or recreate capture flags. The
[bounded playback limits](v2-replay.md) apply. Configure the union of the original
ownership/open-drain masks on one stopped engine before loading that program.

## Evidence

Python tests cover both bit periods, valid/faulty stop bits, deterministic JSON
round trips, VCD timestamps, simultaneous RX, exact waveform playback and
rejection of altered firmware, shifted capture timestamps, misleading fault
schedules and incomplete captures.

The pin-driven test loads the same firmware over SPI, applies the asynchronous
source independently, decodes the physical output, reads RX and capture data
over SPI and compares every captured event with the model. Hardware ARM occurs
before the host start transaction; record comparisons align the trigger epoch
and retain all relative cycle spacing. Host stop latency changes the final
quiet capture span. Test artifacts include fault/fixed JSON and VCD examples.
