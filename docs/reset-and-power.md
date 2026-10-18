# Reset, selection, and state loss

Silicon state is not assumed to start at zero. Before reset, the ordinary RTL
and functional gate-level model may expose unknown status and output state.
A valid host interaction starts only after the external reset and internal
reset synchronizer have completed. Digital simulation does not model voltage
ramps, pad isolation, brownout, or analog recovery/removal constraints.

## Required restart sequence

After power loss or selection of a design whose state is not guaranteed:

1. Keep protocol output use disabled and assert `rst_n` low. Supply the system
   clock; the examples hold reset for ten 40 ns cycles.
2. Select/enable the design, release reset, and allow the two-stage reset
   synchronizer to settle. The examples wait ten further cycles.
3. Query the version/capabilities and check stopped, empty state.
4. Reapply ownership/open-drain masks, watches, abort masks, input filtering,
   and other required configuration. Reload program words and refill TX data.
5. Arm capture if required, then start execution. Treat earlier capture and
   queued RX/TX data as lost.

External reset and logical disable must release output enables. Unused memory
contents may remain unknown after reset; validity bits and counts must prevent
those contents from appearing as successful externally observed data.

A powered logical `ena=0` interval is different from power loss. It stops
engines and clears transport/queue state according to their reset paths, while
program validity/configuration can remain available. Capture stops on disable
and retains its recorded contents while powered. None of this establishes
retention when the selected design loses power. Use the restart sequence after
state loss even if a logical-disable simulation retains data.

## Initializer audit

Source inspection includes RTL, formal sections, testbenches, simulation
options, cell models, and Python models/scoreboards. Yosys elaboration before
optimization confirms that both ordinary tops have no initialized state and
that all program/FIFO/capture memories have unconstrained initialization.

| Location | Initialization | Classification |
| --- | --- | --- |
| `src/engine.v`, under `FORMAL` | `reg past_valid = 0` | Formal history bookkeeping |
| `src/v2_engine.v`, under `FORMAL` | `initial past_valid = 0` | Formal history bookkeeping |
| `src/v2_capture.v`, under `FORMAL` | `initial past_valid = 0` | Formal history bookkeeping |
| `src/v2_input_filter.v`, under `FORMAL` | `initial valid = 0` | Formal history bookkeeping |
| `src/v2_project.v`, under `FORMAL_INTEGRATION` | `initial formal_past_valid = 0` | Formal history bookkeeping |
| `test/tb.v` | Waveform dump setup and a delay | Testbench instrumentation; no DUT initialization |
| `test/reset_tb.v` | No `initial` block or declaration initializer | Testbench inputs are driven explicitly by cocotb |
| Pinned `sg13cmos5l_stdcell.v`, `sg13cmos5l_sighold` | Conditional warning messages under `DISPLAY_HOLD` | Cell-model diagnostic; option is not enabled |
| Pinned `sg13cmos5l_udp.v` | No initialization block | Sequential UDP state remains four-state |

Wire declaration assignments are combinational connections, not initialized
storage. Python reference models construct reset architectural state; existing
scoreboards reset the DUT before comparison. The new cold-start checks do not
use a zero-filled reference model to decide startup correctness. Pin decoders'
initial idle bus levels describe driven stimulus rather than initial flop state.

The PDK is `2bbec755dc67ca3db0261c3d6163e15735d66710`. Gate-level audit reports
retain the hashes of both actual cell-model files and the tested netlist.
`FUNCTIONAL` and `SIM` select the established functional simulation models;
this audit does not annotate SDF. Routed timing acceptance remains separate.

## Executable four-state checks

```sh
python -m tools.audit_initialization --output build/initialization-new
python -m tools.validate_reset --output build/reset-rtl-new
python -m tools.validate_reset --version 2 \
  --netlist /absolute/path/to/tt_um_mcranny_protocol_emulator_v2.v \
  --pdk-root /absolute/path/to/pinned-pdk \
  --output build/reset-gatelevel-new
```

Use fresh output directories. Reports preserve commands, source identity,
checksums, XML, logs, and the actual unknown values. The runner selects Icarus,
sets `COCOTB_RESOLVE_X=error`, and removes inherited test filters and simulator
argument overrides. It does not pass zero-initialization flags or initialize
DUT memories. Conversion of a startup X to an integer must raise an error.

The testbench contains two copies of the selected top. One is exercised through
startup, reset, program loading, activity, a partially transmitted host frame,
and powered disable/reselection. The other remains unclocked and unreset until
it replaces the first copy. Unknown state is observed again immediately before
its reset, proving that the replacement did not inherit the first copy's data.
This models complete loss of internal state; it is not a transistor-level
power-switch model. Direct DUT output-enable checks are made while disabled,
then reset, reconfiguration, reload, and execution must succeed on replacement.

The tests reject unknown values on observed post-reset status, host responses,
output values, and enables. They verify invalid program/start rejection, empty
FIFO status, and zero capture reads outside the valid range. They deliberately
do not require unused memory storage itself to become known.

## Evidence status

V1 and V2 RTL cold-start/state-loss checks have passed in development. The V1
check also passed against the netlist from manual TT run `38115576757`, whose
source is `3d5210a`. That result is retained separately and is not V2 evidence.
The V2 gate-level audit must run against the V2 qualification netlist once it
is available, and again against the final candidate's netlist. These checks do
not claim hardware operation or completed final release qualification.
