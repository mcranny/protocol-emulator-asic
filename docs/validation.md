# Validation record

## Initial scaffold: physical baseline passed

Source: `fe0a027824e66360c6543fef5b501daa13a169be`.

- [RTL CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810572): passed.
- [Documentation CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810548): passed.
- [Physical run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810547): GDS, precheck, and gate-level jobs passed. The optional viewer failed because GitHub Pages was not configured.
- 6x4 die: 1289.28 x 710.64 um; core area 902417 um2.
- Standard-cell area excluding fill: 290.304 um2; utilization 0.03217%.
- Routed setup/hold violation counts: zero at fast/1.32 V/-40 C,
  slow/1.08 V/125 C, and typical/1.20 V/25 C nominal-RC corners.
- Worst input/output setup slack: 23.1864 ns; hold slack: 15.8569 ns.
- Routing DRC, Magic DRC, and LVS errors: zero. All nine prechecks passed.

This combinational scaffold has no register-to-register paths. Its timing
results validate the flow setup, not the programmable engine's clock rate.

## Integrated V1 candidate: reviewed 2026-09-19

Reviewed source: `b8493a490f751eb550bc3b5ed35d37ada98ad0c2`.
PRs #1 through #5 were merged on 2026-09-19. The merged tree at `f7711a5`
is identical to this reviewed source; later documentation changes do not
constitute a new physical validation.

- [Functional CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35414846609):
  68 Python tests, three core differential suites, three pin-level SPI/UART
  suites, Verilator lint, and Yosys temporal induction passed.
- Core differential suites compare more than 26,000 system cycles, including
  twelve fixed random seeds. Pin-level tests independently decode both UART
  rates and check interruption safety and concurrent host traffic.
- [Physical run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35414846616):
  GDS and all nine prechecks passed. Gate-level elaboration failed, and the
  dependent acceptance job was skipped. **V1 is not release-ready.**

### Routed metrics

The submission's commit_id.json matches the reviewed source and physical run.
Metrics below are from GDS_logs/runs/wokwi/final/metrics.json, checked against
the submission metrics and post-route STA reports.

| Metric | Result |
| --- | --- |
| Die / core area | 916214 / 902417 um2 (6x4 tiles) |
| Standard-cell area, excluding filler | 235839 um2 |
| Standard-cell utilization | 26.1342% |
| Sequential cells | 1943 |
| Target period | 40 ns (25 MHz) |
| Routing DRC / Magic DRC / LVS errors | 0 / 0 / 0 |
| Precheck tests | 9 passed, including KLayout CMOS5L DRC |

| Nominal-RC corner | Worst setup slack (ns) | Worst hold slack (ns) | Register-to-register setup slack (ns) |
| --- | ---: | ---: | ---: |
| Fast, 1.32 V, -40 C | 22.685685 | 0.109883 | 38.438278 |
| Slow, 1.08 V, 125 C | 21.130863 | 0.611260 | 23.099981 |
| Typical, 1.20 V, 25 C | 22.119335 | 0.290114 | 30.822704 |

Reported setup/hold violation counts are zero at each corner. These are
configured-flow STA results, not measured silicon performance, an Fmax sweep,
or complete electrical signoff. The unused 73.8658% of core placement area
does not guarantee capacity for that much additional logic or routing.

### Outstanding release blockers

1. Gate-level compilation lacks definitions for `ihp_dff_r`, `ihp_mux2`, and
   `ihp_mux4` (1943, 1413, and 257 references respectively). The test Makefile
   lists IO and standard-cell models but not their required primitive models.
   Resolve the source list against the pinned PDK and rerun the three suites.
   No integrated gate-level waveform or results XML was produced; this is a
   simulator elaboration failure, not a passing or failing UART simulation.
2. Post-route reports contain **55 slow-corner slew violations** and **123
   fanout violations at each corner**. Worst reported slew is 3.282584 ns
   against a 2.507400 ns limit. Fanout reports include clock-tree leaf buffers
   with 19 loads against a limit of 8. Investigate buffering, sizing, clock
   tree configuration, and applicable library constraints; positive setup
   slack does not resolve these violations.
3. `tools/check_physical.py` currently checks geometry/LVS and setup/hold, but
   does not enforce slew, fanout, or capacitance limits. Strengthen the checker
   and rejection tests before using it for release acceptance. Do not waive
   or relax limits just to obtain a green workflow.

### Artifact audit

- `test-results`: both three-suite XML reports have no failures or skips;
  formal-engine.log reports induction success. The artifact also contains
  tb.fst and UART JSON records with 25-word firmware, vectors 00/55/A5/FF,
  and 15359 / 23039 sampled cycles for 25 / 217 cycles per bit.
- Python's 68-test result is in the CI log, not a retained Python JUnit file.
  Fixed differential seeds are in the test source; first-divergence diagnostics
  are generated on failure. Add a consolidated manifest and Python JUnit
  output as a follow-up evidence-retention improvement.
- `tt_submission`: GDS, OAS, LEF, routed netlist, nominal SPEF, metrics,
  configuration, source identity, and PDK identity are present.
- `GDS_logs`: final metrics and per-corner post-route reports are present.
- `precheck_reports`: all nine tests passed; DRC XML reports are retained.
- `gatelevel_test_results` and `physical-summary` are absent because
  elaboration failed and acceptance was skipped. Never substitute RTL XML
  for the missing gate-level evidence.

See [Phase 2 implementation sequence](phase-2-plan.md) for closure priorities,
throughput limits, and the proposed next capability gates.

## Reproducibility and release gate

Direct actions and CMOS5L support tools are pinned. LibreLane is 3.1.0.dev3;
the pinned action installs IHP-Open-PDK revision
`2bbec755dc67ca3db0261c3d6163e15735d66710`. Nested actions and some dependencies
remain dynamically resolved; this is not a fully hermetic toolchain.

Download the run's GDS_logs, precheck_reports, and gatelevel_test_results
artifacts to separate directories, then run:

```sh
python tools/check_physical.py \
  --metrics GDS_logs/runs/wokwi/final/metrics.json \
  --config GDS_logs/runs/wokwi/resolved.json \
  --precheck precheck_reports/results.xml \
  --gatelevel gatelevel_test_results/results.xml
```

The checker rejects absent timing corners, unconstrained-path sentinel values,
negative slack, physical violations, missing tests, failures, and skipped tests.
Its inputs must come from the same run and source commit; confirm the workflow
head SHA and submission commit_id.json before interpreting its summary.

Retain the submission's pdk.json, resolved.json, commit_id.json, metrics, GDS,
netlist, and regression results with the release. V1 cannot be tagged until all
required checks pass. GitHub Pages is optional and opt-in through the repository
variable ENABLE_GDS_VIEWER after Pages configuration.
