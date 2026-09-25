# V2 candidate contract

This document describes the implementation on the V2 development branch. It
does not establish a V2 release, physical acceptance, or hardware operation.
The released V1 top, pinout, configuration and `v1.0.0` artifacts remain intact.
The integration candidate is `tt_um_mcranny_protocol_emulator_v2`.

## Architecture

Two engines run independently at 25 MHz. Each has 64 24-bit instructions, two
16-bit registers, a 16-bit output shifter, an 8-bit input shifter, 16 byte TX
entries and 16 RX entries containing an 8-bit value and 8-bit metadata.
Program storage has a validity map. Loading software fills unused words with
HALT and verifies the entire image before execution.

The 128-word option is retained in the Python feasibility model only. Candidate
RTL has 64 words; loading a branch outside that memory must fail before START.
The current combined I2C controller and bidirectional I2C target each fit exactly
64 instructions. This leaves no spare instruction capacity for those images;
changes must repeat firmware-fit and timing tests before the ISA can be frozen.

SPI controller firmware accepts `frequency=100_000` or `1_000_000`. Its delay
constants include output, input and loop-control instructions. The selected
SCK period is exactly 250 or 25 system clocks within each byte, for every mode.
At 1 MHz the leading/trailing phases are 12/13 clocks; at 100 kHz both are 125
clocks. CS is deasserted between bytes. Independent target tests cover both
rates, all four modes and asynchronous input phases. Abort and underrun
acceptance still requires expanded testing.

I2C controller firmware accepts `frequency=100_000` or `400_000`. Delays account
for instruction overhead and synchronized clock observation. Byte boundaries,
repeated START and stretching can lengthen periods. Model and pin checks enforce
the clock-frequency ceiling and minimum high/low times from
[UM10204 rev. 7, table 11](https://www.nxp.com/docs/en/user-guide/UM10204.pdf).
This is only part of I2C acceptance: input spike filtering, SDA timing, bus
recovery and analog pad/board behavior are not established by these checks.

Program writes require a stopped selected engine. Individual START preserves
both FIFOs and sticky errors while clearing working registers and events.
Atomic START validates every selected engine before starting any of them.
STOP/HALT release outputs and preserve queues. State reset clears working state,
queues and errors while preserving program validity. External reset additionally
invalidates programs and clears ownership/watch configuration. Disable clears
execution state and queues, retains program/configuration and errors, and does
not resume execution automatically.

The integrated capture candidate stores 32 timestamped 64-bit records without
controlling engine execution. Its trigger, sampling, completion and readout
contract is in [v2-capture.md](v2-capture.md). Combined physical acceptance is
still required; the earlier dual-engine route did not contain capture.

Ownership masks must be disjoint and can only be changed with both engines
stopped. Input observation is shared. An open-drain mask must be a subset of
ownership. A requested high on an open-drain pin produces high impedance;
the output data path is forced low. Reset and disable mask output enables
without waiting for a clock edge.

RX overflow preserves all queued records, rejects the new record, sets the
overflow error and halts the affected engine. Busy operations and TX capacity
rejections do not corrupt active state. Illegal shared host requests and
malformed frames stop both engines. A fatal engine error affects that engine.

## Instruction extensions

V1 opcodes 0 through C retain their encodings and cycle timing. The Python V2
codec reserves a seventh branch-address bit for feasibility experiments; a
64-word RTL candidate faults on a taken address outside its memory.

Opcode D uses bits 19:16 as a subopcode. Fields below are listed from least
significant to most significant; omitted bits are reserved and zero.

| Subopcode | Instruction | Fields / effect |
|---|---|---|
| 0 | IN pin, msb | pin:3, direction:1; shift one input bit into 8-bit ISR |
| 1 | RX metadata | metadata:8; append `(ISR, metadata)` without clearing ISR |
| 2 | CLEARIS | clear ISR |
| 3 | BRPIN target, pin, level | target:7, pin:3, level:1 |
| 4 | BREQ target, byte, register | target:7, byte:8, register:1; compare full register |
| 5 | BRC target, condition | target:7, condition:4 |
| 6 | DRIVE value, enables | value:8, enables:8; atomic output update |
| 7 | WAITFOR pin, level | pin:3, level:1; R1 timeout; retire with flags |
| 8 | MARK mask | mask:8; one-cycle event marker |
| 9 | WAITEVENT mask | mask:8; wait for a latched watch event using R1 timeout |
| A | MOVIS register | register:1; zero-extend ISR |
| B | OUT8 pin | pin:3; shift the high bit of the low OSR byte, zero fill |
| C | PULLNB register | register:1; nonblocking dequeue; retain register if empty |
| D | CLEAREVENT mask | mask:8; clear selected latched watch events |
| E | FAIL code | code:8; sticky firmware detail and fatal firmware error |
| F | BRDIFF target, pin | target:7, pin:3; branch if input differs from requested output |

Conditions 0..6 are TX empty, RX full, last wait timed out, last wait interrupted,
watch event 0, watch event 1, and last PULLNB empty. Other conditions are illegal.

Opcode E subopcodes 0..3 are CALL, RET, UNLINK and WAITBR. CALL has a seven-bit
target and a two-entry return stack; overflow, underflow and a last-word CALL
fault. UNLINK clears the stack. WAITBR fields are target:7, pin:3, level:1; it
branches on interrupt/timeout and otherwise retires normally. It does not clear
the return stack. Opcode F and unused opcode E encodings are illegal.

Nonblocking and branching instructions take one cycle. Waits use R1 without
modifying it; zero is illegal. A matching input wins over timeout. A matching
watch abort sets `interrupted` even if the pin matches on the same edge; WAITBR
then takes its branch. Timeout/interrupt flags change only when a wait retires.
V1 WAIT remains fatal on timeout. Events latch before instruction observation;
CLEAREVENT wins over setting the same event on that edge.

Each engine has two generic input watchers `(mask, value, edge_mask)`. An event
occurs when the synchronized input matches mask/value and an edge-mask bit
changed since the preceding edge. Wait abort masks select these events. I2C
firmware configures START/STOP watchers; SPI target firmware watches CS release.
These comparators do not decode protocol bytes or make protocol decisions.

## Host transport

The physical framing is retained: mode 0, MSB first, exactly 40 clocks per CS,
1 MHz maximum, at least 200 ns high/low and CS setup/hold/inactive intervals.
All crossings use the system clock. Protocol inputs have the same 2–3 cycle
synchronization latency as V1. Dedicated pins are ui0 SCK, ui1 CS_n, ui2 MOSI,
uo0 MISO; uo1/uo2 are engine running flags, uo3 is aggregate error. Protocol
pins remain uio0..7.

A request is command, address and 24-bit payload. Address bit 7 selects an
engine; bits 5:0 select an instruction address where applicable. Bit 6 is
reserved. Global commands require address zero. A response is command (bit 7
set on rejection), echoed address and 24-bit result, delivered during the next
complete frame. Pipelining avoids a NOP after every request; one final NOP
flushes a batch. The Python API validates up to 64 complete request frames before
sending any of them. Frame failures are not automatically retried. A BatchError
preserves responses received before failure and the number of submitted or
attempted requests; the final attempted delivery may be unknown after a
transport exception.

| Command | Operation |
|---|---|
| 00 | NOP |
| 01 / 02 | Write / read selected program word; selected engine must be stopped |
| 03 / 04 / 05 | Start / stop / state reset selected engine |
| 06 | Status: running bit 0, engine/shared error bits shifted left one |
| 07 | V1-shaped single-byte TX push |
| 08 | TX level bits 4:0, RX level bits 9:5 |
| 09 | Safe read-only version probe: `0x020240` |
| 0A | Capabilities: `0x021010` = two engines, 16 TX, 16 RX |
| 0B | Feature bits and capture capacity, currently `0x20001f` |
| 10 / 11 / 12 | Push 1 / 2 / 3 packed TX bytes, low payload byte first |
| 13 | Reserve one RX record for the next response |
| 20 | Atomic START, payload engine mask 1..3 |
| 21 | Ownership: owner low byte, open-drain next byte |
| 22 / 23 | Configure watch 0 / 1: mask, value, edge mask in increasing byte order |
| 24 | Configure wait-abort event mask, low two bits |
| 30..37 | Capture control/status/readout; see [capture contract](v2-capture.md) |

TX packed writes are all-or-nothing against the pre-edge FIFO level. Result
bits 4:0 contain the pre-push level and bits 6:5 the accepted byte count.
A simultaneous engine pop does not create acceptance space at a full boundary.
An empty FIFO receiving a host push is first visible to PULL on the next edge.

RX replies contain data bits 7:0, metadata bits 15:8 and available count bits
20:16. Empty reads reject nonfatally. A reserved record is consumed only when
the subsequent complete frame has shifted its response out. A short/long frame
discards the reservation, halts execution and leaves the record queued for an
explicit new read. Pipelined reads account for the preceding consumed record.

`protocol_emulator.v2.host.connect()` probes the version and selects the V1 or
V2 backend. Old host clients are not promised binary compatibility with V2.
The Python API supports engine-specific loading/readback, configuration,
control and batched TX/RX. Program readback and instruction execution share
one read port per engine. Readback of a running engine rejects nonfatally;
host access to a stopped engine never stalls the other engine. Stop the
selected engine before either loading or reading its firmware.
The bounded [scenario CLI](v2-scenarios.md) runs simulation; complete hardware
scenario execution remains required before release.

## Verification status and limits

Run `make v2-test` in the established Python/tool environment. Candidate core
differential tests compare architectural state after every edge. Independent
model and pin peers exercise firmware without using engine instruction timing
as their decoding oracle. Formal induction covers FIFO counts, ordering and
data preservation for an arbitrary accepted TX byte/RX record, return-stack
bounds, output ownership, release and open-drain masking. Instruction fetch is
overapproximated as an arbitrary word each cycle; reset is assumed on the
initial sampled edge. This is stronger for these safety properties but does
not prove program-storage correctness, complete protocol correctness or capture.
Run `python tools/v2_mutations.py` to require that dropped RX data, incorrect
packed-TX byte order, unsafe open-drain enabling, inverted wait edges and shifted
capture timestamps are detected by the tests. Capture has separate induction
and differential checks through `make v2-capture-test`.

The pin-driven sustained-UART test sends 145 TX and receives 128 RX bytes with
a 500 us service interval. Its largest service occupies 492 us, including
40-clock SPI frames and gaps. This proves the tested simulation budget and
byte/frame continuity, not physical host scheduling. The API requires its
caller to schedule service; it makes no real-time Linux guarantee.

Scenario/replay, additional error/phase sweeps, integration mutation coverage, the custom
protocol demonstration, full physical/precheck/gate-level acceptance, and the
public release evidence package remain required. The candidate top is not
selected by `info.yaml`, and existing V1 acceptance must not be presented as V2
acceptance.

## Reproducing physical feasibility

Prepare a fresh isolated source directory with
`python tools/v2_candidate.py --output build/v2-candidate-01`, then pass that
directory to the pinned runner described in `docs/local-build.md` using
`--source build/v2-candidate-01`. Preparation refuses to overwrite an existing
directory and records input hashes, including firmware, verification and flow
configuration. The runner preserves that manifest with the route evidence.
The release `info.yaml` and physical configuration are not replaced.

`physical/v2/overrides.json` currently experiments with 100 um clock-buffer
spacing and a 150 um maximum wire length for global-route design repair.
These are implementation settings; the 6x4 footprint, 40 ns clock, 1.5 ns
transition limit and all existing acceptance checks remain in force. The
settings do not establish physical closure. Route-only success must still be
followed by full physical, precheck and routed-netlist acceptance.

The independent pin suite can run on a functional routed netlist using
`make -C test -f Makefile.v2pins GATES=yes PDK_ROOT=/absolute/pdk/path
GATE_LEVEL_NETLIST=/absolute/routed/netlist.v`. This requires the unpowered
netlist and the pinned standard-cell models. Its results are separate from
RTL (`results_v2_gl.xml`) and from extracted timing analysis; it does not
replace all-corner timing or physical checks.
