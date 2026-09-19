# Verification approach

Run `make test` with Python 3.11+, the pinned test requirements, Icarus Verilog,
Verilator, and Yosys on PATH. `make synth` produces a technology-independent
cell report; this is not the mapped CMOS5L area or routed timing result.

## Independent checks

- Python tests use hand-encoded words, expected PC sequences, and independent
  bit-cell schedules in addition to assembler round trips.
- `test/test_engine.py` compares architectural state after every system edge:
  PC, registers, output shifter, output values/enables, FIFO level, error flags,
  and delay/wait counters. It compares host responses before execution.
- Twelve fixed random seeds exercise instructions and concurrent host access.
  Failures save the seed, program, recent stimulus, and first divergence under
  `test/output/`. UART tests save firmware and sampled pin waveforms.
- `test/test.py` operates through top-level pins only. Its independent UART
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
inputs enter two-stage synchronizers. The host contract requires >=200 ns high,
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
