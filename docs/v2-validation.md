# V2 validation

The [architecture and host interface](v2-architecture.md),
[capture](v2-capture.md), [replay](v2-replay.md), and
[scenarios](v2-scenarios.md) describe implemented behavior.
The [design evolution](v2-design-evolution.md) explains the measured decisions.

## Functional checks

The [verification write-up](verification.md) gives the complete functional
command and the [reset audit](reset-and-power.md) documents four-state checks.
The full gate retains Python identities, both tops' lint/synthesis, all ordinary
core/pin/capture scenarios, both RTL reset scenarios, seven initialization
elaborations, five induction proofs, and six mutations in a new output directory.

## Physical and acceptance checks

The [stock TT procedure](v2-tt-flow.md) is the submission-flow qualification
path. The repair and checkpoint tools below remain retained diagnostic tools;
they are not the timeout fallback. `tools/v2_candidate.py` prepares a new
candidate with pinned constraints.
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

The [local acceptance index](https://github.com/mcranny/protocol-emulator-asic/blob/447b3dfd0986d473c71afa28efafb3a5cff48da7/evidence/v2-local-af573c5/README.md) preserves
the successful run and exact source identity. Check that archive independently
from functional validation of this branch. The current Draft PR remains open
for additional behavioral tests, formal properties and coverage review.
