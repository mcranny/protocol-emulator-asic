# Physical implementation and release evidence

The V2 release candidate is being prepared. Passing functional checks or a
historical local route does not establish acceptance of the final revision.
No silicon/FPGA demonstration or competition submission is claimed.

## Fixed configuration

The candidate remains 6×4 tiles at 40 ns clock period, with maximum transition
1.5 ns, clock uncertainty 0.25 ns, and 32 capture records. The anticipated 8×4
template is deferred; this plan includes no migration or re-hardening for it.
The [stock TT procedure](v2-tt-flow.md) binds original/prepared inputs and exact
action, support tools, LibreLane, container and PDK versions to the results.
Its repair-provenance table distinguishes local fresh routing, diode power
reconnection and guarded fanout repair from operations performed by stock TT.

## Evidence boundaries

| Evidence | Source and meaning |
| --- | --- |
| Frozen baseline/migration | `3d5210adf0edc53b635738850861f639996f2d4a`: 583 pytest cases, 24 RTL scenarios, five proofs and six mutations; no deprecated cases removed |
| Published functional candidate | `447b3dfd0986d473c71afa28efafb3a5cff48da7`: 631 pytest cases and complete pre-reset-audit gate, locally and in hosted CI |
| Stock V2 qualification | [Run 38155699349](https://github.com/mcranny/protocol-emulator-asic/actions/runs/38155699349), source 447b3df; consult retained final result before making a physical-pass claim |
| Manual TT run | [Run 38115576757](https://github.com/mcranny/protocol-emulator-asic/actions/runs/38115576757), source 3d5210a, produced the V1 top; it does not qualify V2 |
| Historical local acceptance | `af573c5fef6f96b4c2fbd400e6565b3b41f54aef`; [immutable evidence index](https://github.com/mcranny/protocol-emulator-asic/blob/447b3dfd0986d473c71afa28efafb3a5cff48da7/evidence/v2-local-af573c5/README.md) retains its original source identity |
| Reset/formal development | Expanded local gate includes 644 pytest cases, two additional RTL reset scenarios and seven initialization elaborations; final V2 netlist audit remains required |

Executable migration measurements preserve the same production executed lines
and branch arcs: 2948/4745 lines and 1197/1912 branches at the Step 1 checkpoint.
The [frozen migration record](https://github.com/mcranny/protocol-emulator-asic/tree/447b3dfd0986d473c71afa28efafb3a5cff48da7/evidence/test-migration-3d5210a)
accounts for every baseline pytest case and cocotb scenario. Those measurements
are not a claim of complete RTL or instruction-path coverage.

## Final qualification checklist

- Freeze a clean source revision after reviewing the latest CMOS5L action tip.
  Record the checked SHA/date and rerun if compatible dependencies changed.
- Pass the full functional gate, strict HTML build and executable examples on
  that source, with complete collections, tool versions, logs and hashes.
- Approve and run fresh stock TT qualification, including required zero physical
  violations, all-corner timing/electrical results, precheck and the full V2 pin suite.
- Run four-state reset/state-loss checks against the matching produced V2 netlist.
- Assemble matching source/netlist/GDS/full-geometry evidence and pass unchanged
  acceptance requirements. A physical failure is a submission-level blocker.
- Retain source/package archives, Sphinx HTML, generated example programs/traces,
  checksums, compatibility/limitation statements, release notes and the evidence index.
- Review eventual version `2.0.0rc1` metadata for the `v2.0.0` target. Merge,
  tagging, release publication and competition submission remain separate actions.

If the six-hour hosted job times out, preserve that timeout and use the same
pinned TT flow locally as documented. The report must say `execution_path` is
`github_actions` or `local_tt_flow`, record the bundle checksum/location and
original run, and distinguish CI recording of local results from rerunning them.
A stock-flow design violation is not eligible for substitution by custom repairs.
