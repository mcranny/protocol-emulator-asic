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

## Integrated V1 candidate

Source: `55c5f9256fa9bb82ad9b266026e0852f9f2482b6`.

- [Functional CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35414563779): passed.
- Clean-checkout local validation: 59 Python tests, three core differential
  suites, three pin-level SPI/UART suites, Verilator lint, and Yosys temporal
  induction passed.
- Core differential suites compare more than 26,000 system cycles, including
  twelve fixed random seeds. Pin-level tests independently decode both UART
  rates and check interruption safety and concurrent host traffic.
- [Physical run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35414563782): pending. No integrated mapped-area or timing acceptance is claimed yet.

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
