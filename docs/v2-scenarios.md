# Protocol fault scenarios and replay

The V2 simulation scenario runner demonstrates a scheduled malformed stop bit,
independent UART reception, triggered capture, JSON/VCD export and both replay
forms. It exercises the v2 ISA and has pin-driven RTL checks. Combined
physical and routed-netlist acceptance use the source-matched
[V2 evidence bundle](v2-validation.md).

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

## Scenario specification

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
preloaded TX queues, and external changes on unowned or owned open-drain pins. Owned pins feed their
driven values back through the input synchronizers; released pins observe the
external idle level. Independent decoders observe the selected UART, SPI or I2C waveform. Mid-run
host servicing, input watchers and board electrical models are outside this
bounded scenario runner.

Replay validates versions, firmware hashes, pin ownership, timestamps and
capture completeness before executing. It reuses the original external
stimulus and compares decoded results and cycle events exactly. The first
differing result category includes expected and actual data in its error.
Fault metadata must match the selected protocol, decoded fault timing and
ownership.
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

## SPI and I2C fault examples

```sh
PYTHONPATH=host python -m protocol_emulator.v2.scenario spi-demo build/demo/spi-fault
PYTHONPATH=host python -m protocol_emulator.v2.scenario spi-demo build/demo/spi-good --without-fault
PYTHONPATH=host python -m protocol_emulator.v2.scenario i2c-demo build/demo/i2c-fault
PYTHONPATH=host python -m protocol_emulator.v2.scenario i2c-demo build/demo/i2c-good --without-fault
```

The SPI example delays one target response transition while an independent
controller supplies SCK and CS. The decoder observes 00 instead of the expected
80. The I2C example omits the target ACK for address byte 84; restoring the ACK
produces a passing decode. These are bounded scheduled output demonstrations,
not substitutes for the general SPI/I2C firmware tests. Each capture fits all
required events in the 32-record store without truncation. Replay and playback
use the same commands as the UART example.

`prepare_hardware(device, scenario)` validates replay before host I/O, stops and
resets both engines, clears ownership, disables input filtering, loads and
verifies both programs, preloads TX and arms capture. It returns the atomic
start mask. The caller supplies the recorded external stimulus and starts the
engines, using the physical running indication to establish the execution epoch.
Host SPI transaction completion is not a precise waveform epoch.
`collect_hardware(device, scenario)` stops capture and compares every record
relative to its trigger, plus RX records and errors. A longer quiet tail caused
by host stop latency is allowed; missing or different events fail. Immediate
capture triggers are not supported by this hardware preparation API.

Pin-driven tests execute all four SPI/I2C fault and corrected cases through the
host interface, compare every output transition and capture record, and decode
with independent protocol observers. This validates the digital host workflow;
no physical board execution is claimed.
