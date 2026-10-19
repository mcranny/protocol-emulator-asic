# Verification write-up

Use Python 3.11+, Icarus Verilog, Verilator, and Yosys, with the pinned
`test/requirements.txt` dependencies. Run the complete functional gate into a
new directory:

```sh
python tools/validate_local.py --require-clean --output build/validation-new
```

For development with uncommitted changes, omit `--require-clean`; the manifest
will explicitly identify dirty-source evidence. The runner requires complete
pytest collection, both tops' lint/synthesis, 24 ordinary RTL scenarios, two
four-state reset scenarios, seven initialization elaborations, all five
induction proofs, and all six detected mutations. Technology-independent
synthesis is not a routed area or timing result. Physical approval and exact
netlist checks follow the TT procedure.

## Independent checks

- Python tests use hand-encoded words, expected PC sequences, and independent
  bit-cell schedules in addition to assembler round trips.
- `test/v1/test_engine.py` compares architectural state after every system edge:
  PC, registers, output shifter, output values/enables, FIFO level, error flags,
  and delay/wait counters. It compares host responses before execution.
- Twelve fixed random seeds exercise instructions and concurrent host access.
  Failures save the seed, program, recent stimulus, and first divergence under
  `test/output/`. UART tests save firmware and sampled pin waveforms.
- `test/v1/test_pins.py` operates through top-level pins only. Its independent UART
  decoder checks every bit cell and frame spacing at both specified rates.
  The same suite runs against the submitted netlist in the gate-level job.

## Formal scope

`formal/engine.ys` uses Yosys SAT temporal induction, with a mandatory reset on
the initial sampled edge. Other inputs, commands, input pins, and enable are
unconstrained. Uninitialized memory data are unconstrained; reset invalidates
their validity bits. The properties establish FIFO bounds, output release,
error containment, illegal-fetch halting, and busy-write program-data/validity preservation.
The six-bit PC bound is a structural invariant; last-word fallthrough behavior
is separately tested. This proof does not establish UART correctness or analog
metastability behavior. An induction success is required; a bounded pass alone
does not satisfy this target.

## Clock/reset crossing review

All sequential logic uses the system clock. SPI SCK, CS, MOSI and protocol
inputs enter two-stage synchronizers. The host specification requires >=200 ns high,
low, CS setup, hold, and inactive intervals, providing five system periods per
minimum interval; 1 MHz SCK is the supported limit. MISO is prepared after the
detected falling edge, before the next rising edge. Inputs have no guaranteed
recognition below the documented minimum pulse widths. Output-enable gating
releases pins on external reset and disable independent of instruction state.

The Tiny Tapeout enable input is a selection/control signal, not a generated
clock. Reset asserts asynchronously and deasserts through a two-stage
synchronizer before reaching the host and engine. External wiring must satisfy pad electrical requirements; these digital
checks cannot establish voltage compatibility or silicon operation.

## Physical release gate

The pinned CMOS5L flow must provide mapped area, utilization, routed setup/hold
results at its required corners, DRC/LVS/precheck results, and gate-level test
results tied to the source commit. The default template does not run every
possible independent DRC engine; report which checks the flow actually ran.
GitHub Pages/viewer deployment is a presentation step, not a physical criterion.

## V2 proof and reset scope

The [five induction proofs](formal-proofs.md) document their assumptions and
black boxes. The [reset and power audit](reset-and-power.md) adds four-state
startup and state-loss checks. Track 2 records expanded
RTL verification and the Lean semantics work after the candidate baseline.

## V2 differential, pin and fault coverage

V2 core simulation compares architectural state against the independent Python
model at clock edges. The capture differential suite covers timestamped records,
triggering, truncation and host readout. Fourteen pin scenarios exercise the
complete V2 host transport and firmware. Independent UART decoders sample bit
centers; SPI peers decode clock edges for every mode; I2C peers resolve open-drain
lines and check ACK/NACK, repeated START, STOP and stretching. Expectations are
not derived from the engine's current instruction or delay counters.

Boundary and fault cases include FIFO full/empty admission, rejected busy writes,
invalid image/address/encoding, malformed host frames and RX reservations,
UART mismatch/false starts/framing/break, SPI partial transfers/abort/starvation,
I2C NACK/arbitration/stuck lines/bus clear, capture boundaries and ownership
conflicts. Seeds and failure traces are retained by the suites. The
[example matrix](example-programs.md) links runnable demonstrations to these tests.

The six mutations require detection of dropped RX data, swapped packed TX order,
unsafe open-drain enable, inverted wait edge, shifted capture timestamp and
accepted ownership overlap. Mutation sensitivity covers those chosen defects;
it is not an estimate of all possible fault detection.

## Migration, findings and limitations

The frozen Step 1 migration accounts for all 583 original pytest cases and all
24 original cocotb scenarios. No case was confirmed deprecated and none was
deleted. Exact production executed-line and branch-arc sets were preserved
(2948/4745 lines, 1197/1912 branches). Added qualification/checker and reset tests
bring the development Python suite to 651 cases; two reset scenarios are additional
to the 24 ordinary RTL scenarios. See [source-bound records](release-evidence.md).

Both ordinary elaborated tops have no initialized synthesized state. Seven
ordinary/formal elaborations permit only formal history bookkeeping initializers;
memories remain unconstrained. Four-state startup tests observe actual unknowns,
reject X-to-zero conversion, then check reset, active reset, logical deselection
and replacement with a cold instance before reload. The [audit](reset-and-power.md)
records the limits of that modeled state loss. V2 final-netlist checks remain
required before qualification.

The five induction proofs establish their listed safety properties under
[explicit assumptions and abstractions](formal-proofs.md). They do not prove
complete protocol semantics, analog timing or silicon behavior. Historical local
physical acceptance is bound to af573c5; stock TT closure for each new candidate
requires fresh evidence. Track 2 defines expanded
properties, compositional-assumption review, broader mutations and Lean ISA
encoding/execution/timing theorems, beginning with a property-to-implementation
map. Those are planned deliverables, not V2 candidate proof claims.
