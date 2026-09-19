# Validation record

## Current status

V1 is implemented but **not released**. Functional verification passes; routed
electrical closure is still in progress in [PR #9](https://github.com/mcranny/protocol-emulator-asic/pull/9).
The primitive-model fix and stronger release checks are merged. Development
merges are not physical acceptance, and no FPGA or silicon operation is claimed.

Latest reviewed physical source: `c22a3d440bb1bf25fe691259c937f95bc4e683c3`.
[Run 35460188674](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35460188674)
passed its functional job and failed the enforced slew checks. Its precheck,
gate-level, and final acceptance jobs were consequently skipped.

| Latest candidate metric | Result |
| --- | --- |
| Python tests | 176 passed |
| RTL suites | 3 core differential + 3 pin-level passed |
| Lint / formal induction | Passed |
| Die / target | 1289.28 x 710.64 um (6x4) / 40 ns (25 MHz) |
| Standard-cell area / utilization, excluding filler | 241531 um2 / 26.7649% |
| Worst routed setup / hold slack | +21.090696 / +0.100768 ns |
| Slew violations, fast / slow / typical | 0 / 125 / 23 |
| Fanout violations | 1 at each corner |
| Capacitance / Magic DRC / LVS errors | 0 / 0 / 0 |

Slew counts use the candidate's conservative **1.5 ns** constraint; they cannot
be compared directly with counts under the earlier library limits. Positive
setup/hold slack is not electrical signoff or an Fmax measurement. Available
placement area is not a guarantee of equivalent capacity for new features.

## Evidence history

- Scaffold `fe0a027`: [GDS, precheck, and gate-level passed](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810547).
  This tiny combinational baseline validates flow setup, not the engine.
- Integrated `b8493a4`: [physical run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35414846616)
  completed GDS and nine prechecks but gate-level elaboration lacked the IHP
  UDP primitives. It had 55 slow-corner slew and 123 fanout violations;
  standard-cell area was 235839 um2. The older checker did not enforce these
  electrical metrics. This was not a release-quality physical pass.
- The model-source fix includes `sg13cmos5l_udp.v`. All three pin suites passed
  locally on that routed netlist and again on the `f3e605e` routed netlist,
  including UART at both rates. Those results do not transfer signoff to a
  later netlist and do not resolve electrical violations.
- `f3e605e`: [first closure run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35429649064)
  reduced fanout violations to three but retained 55 slow-corner slew
  violations. `c22a3d4` added a post-antenna repair pass and tightened slew;
  its results are above. Neither candidate is accepted.

Failed and cancelled runs remain visible as history; neither supplies passing
acceptance evidence. Experiment details belong with the draft closure PR,
not in the product requirements or the Phase 2 feature list.

## Verification scope and retained artifacts

Core differential tests compare more than 26,000 system cycles, including
twelve deterministic random seeds. Pin-level tests independently decode both
UART rates and test concurrent SPI traffic, starvation, and interruption safety.
See [verification](verification.md) for proof assumptions and coverage limits.

The release workflow retains Python JUnit, both RTL XML reports, formal logs,
waveforms, random programs/stimuli, assembled UART firmware, and source/tool/
checksum manifests. Gate-level evidence additionally identifies the routed
netlist and primitive models. The physical checker rejects missing or violating
slew, fanout, capacitance, geometry, timing, test, and provenance evidence.
It requires all nine prechecks and the three named gate-level suites.

Gate-level simulation here is functional routed-netlist simulation; it does
not replace extracted STA with a claim of delay-annotated simulation.

## Local work and final release gate

Use the [local build instructions](local-build.md) for iterative optimization.
Routine pushes run functional and documentation CI. Expensive GDS workflows
are **manual**, to avoid rebuilding the chip for documentation or intermediate
experiment pushes. The full acceptance requirement has not changed.

After local closure, dispatch the full workflow on the exact candidate:

```sh
gh workflow run gds.yaml --repo mcranny/protocol-emulator-asic --ref BRANCH
gh run list --repo mcranny/protocol-emulator-asic --workflow gds.yaml
```

Confirm that the resulting run's head SHA is the intended source. Require
functional, GDS, precheck, gate-level, and acceptance jobs to pass together.
After any merge, validate the final release revision rather than assuming the
branch's earlier evidence automatically proves the merged commit.

Direct actions and CMOS5L support tools are pinned. LibreLane is 3.1.0.dev3;
IHP-Open-PDK is `2bbec755dc67ca3db0261c3d6163e15735d66710`. Nested actions and
some dependencies remain dynamically resolved; CI is not fully hermetic.

Download artifacts from the same run to the paths below, then independently
check them with the exact workflow head SHA:

```sh
python tools/check_physical.py \
  --metrics GDS_logs/runs/wokwi/final/metrics.json \
  --config GDS_logs/runs/wokwi/resolved.json \
  --precheck precheck_reports/results.xml \
  --gatelevel gatelevel_test_results/results.xml \
  --source-commit FULL_WORKFLOW_HEAD_SHA \
  --submission submission/tt_submission/commit_id.json \
  --rtl-manifest functional/build/rtl-evidence.json \
  --gatelevel-manifest gatelevel-evidence/gatelevel-evidence.json \
  --python-tests functional/build/python-results.xml \
  --core-tests functional/test/results_core.xml \
  --formal-log functional/build/formal-engine.log
```

Here `functional` is the `test-results` artifact, and `submission` is the
`tt_submission` artifact. Retain matching PDK/config/source identities, GDS,
netlist, SPEF, metrics, reports, firmware, and test results with the release.
Do not tag V1 until every mandatory gate passes. GitHub Pages remains optional.
