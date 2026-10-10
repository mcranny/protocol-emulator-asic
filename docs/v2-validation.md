# V2 validation

The [architecture and host interface](v2-architecture.md),
[capture](v2-capture.md), [replay](v2-replay.md), and
[scenarios](v2-scenarios.md) describe implemented behavior.
The [design evolution](v2-design-evolution.md) explains the measured decisions.

## Functional checks

Install `test/requirements.txt` in a Python virtual environment and provide
Icarus Verilog, Verilator and Yosys. Run `make test`, `make v2-test`,
`make v2-capture-test` and `python tools/v2_mutations.py`.
V1 and V2 have independent CI jobs. The V2 job also runs shared tooling tests.

For a complete immutable functional record from a clean checkout:

```sh
python tools/validate_local.py --require-clean --output build/validation-new
```

The runner checks collected Python test identities, both top-level designs,
core/pin/capture simulation, the five existing formal proofs and six mutations.
Outputs are local evidence, not tracked source.

## Physical and acceptance checks

`tools/v2_candidate.py` prepares a new candidate with pinned constraints.
`tools/v2_closure.py` requires fresh all-corner reports and connectivity checks;
`tools/v2_route_trial.py` and `tools/v2_route_resume.py` retain controlled routing
attempts. Use each command's `--help` for required inputs. Reusing a checkpoint
requires matching source, configuration and input hashes.

Acceptance requires functional, physical, precheck, gate-level and full-geometry
render evidence from the same clean source identity. `tools/v2_evidence.py`
records and assembles stage evidence; `python tools/check_v2.py BUNDLE` verifies
it. Missing results, skipped required suites, mixed source inventories and
changed netlist/layout hashes fail the gate. Positive timing or zero routing
violations alone do not establish acceptance. Each new revision needs its own
qualification; historical evidence keeps its original source identity.

## Successful baseline and continuing work

The [local acceptance index](../evidence/v2-local-af573c5/README.md) preserves
the successful run and exact source identity. Check that archive independently
from functional validation of this branch. The current Draft PR remains open
for additional behavioral tests, formal properties and coverage review.
