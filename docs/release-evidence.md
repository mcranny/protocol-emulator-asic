# Physical implementation and release evidence

The V2 release candidate uses the local TT checkpoint recovery of
`447b3dfd0986d473c71afa28efafb3a5cff48da7` as its physical release basis.
The repository owner selected this already uploaded layout on 18 October 2026
after the fresh `881495c` hosted run reached its six-hour limit. This decision
replaces the fresh-route requirement for this release while retaining all
physical checks and the original source identities. No silicon/FPGA
demonstration or competition submission is claimed.

The [public recovery archive](https://evidence.mcranny.net/recovery-447b3df)
contains the layout and original evidence. Its SHA-256 is
`2d742a7604d573617cf5f0471e435858afdd9729505c9c9ebb275c075942b466`.
The [successful CI reporting run](https://github.com/mcranny/protocol-emulator-asic/actions/runs/39067563290)
records that local evidence; it did not rerun physical verification.

## Fixed configuration

The candidate remains 6×4 tiles at 40 ns clock period, with maximum transition
1.5 ns, clock uncertainty 0.25 ns, and 32 capture records. The anticipated 8×4
template is deferred; this plan includes no migration or re-hardening for it.
The stock TT procedure binds original/prepared inputs and exact
action, support tools, LibreLane, container and PDK versions to the results.
Its repair-provenance table distinguishes local fresh routing, diode power
reconnection and guarded fanout repair from operations performed by stock TT.

## Evidence boundaries

| Evidence | Source and meaning |
| --- | --- |
| Frozen baseline/migration | `3d5210adf0edc53b635738850861f639996f2d4a`: 583 pytest cases, 24 RTL scenarios, five proofs and six mutations; no deprecated cases removed |
| Published functional candidate | `447b3dfd0986d473c71afa28efafb3a5cff48da7`: 631 pytest cases and complete pre-reset-audit gate, locally and in hosted CI |
| Reset-audit checkpoint | `c1c9be573005a022346c2eca6d425f17f815a69e`: 667 pytest cases, all original RTL scenarios, both RTL reset scenarios, seven initialization elaborations, five induction proofs and six detected mutations |
| Software/package candidate | `881495cb4d5a6270f5dd7625b1787d83f3059827`: 699 pytest cases and the complete functional gate passed locally and in hosted CI; strict Sphinx HTML and all 14 executable examples passed, including installed-wheel checks |
| Original hosted timeout | [Run 38155699349](https://github.com/mcranny/protocol-emulator-asic/actions/runs/38155699349), source 447b3df; the local recovery retains this parent identity |
| Final hosted timeout | [Run 39087676085](https://github.com/mcranny/protocol-emulator-asic/actions/runs/39087676085), source 881495c: functional gate passed; GDS exceeded six hours, with gate-level/precheck/acceptance jobs skipped. Retained logs reached routing iteration 7 with 1,405 intermediate violations; this is not a completed failing physical result or a physical pass |
| Manual TT run | [Run 38115576757](https://github.com/mcranny/protocol-emulator-asic/actions/runs/38115576757), source 3d5210a, produced the V1 top; it does not qualify V2 |
| Historical local acceptance | `af573c5fef6f96b4c2fbd400e6565b3b41f54aef`; [immutable evidence index](https://github.com/mcranny/protocol-emulator-asic/blob/447b3dfd0986d473c71afa28efafb3a5cff48da7/evidence/v2-local-af573c5/README.md) retains its original source identity |
| Selected physical release | Physical source `447b3df`: recovery completed with zero required physical violations; nine stock prechecks, all 14 V2 gate-level pin scenarios, independent mapping and full-geometry checks passed. Supplemental reset audit from `c1c9be5` also passed on that netlist. See the recovery scope and provenance. |

## Layout and software correspondence

The physical source remains `447b3df`; the package and functional-test source
is `881495c`. On 18 October, both versions of all five V2 RTL inputs were
elaborated with the same Yosys executable (`read_verilog`, `hierarchy -check`,
`proc`, `opt_clean`). Their complete JSON designs are identical after removing
only `src` location attributes. Ports, cells, parameters, connections, memories,
net names and all other attributes are retained in that comparison. Four RTL
files are byte-identical; `v2_project.v` differs only in comments.

The effective configuration (root config plus V2 overrides) and the V2 TT
metadata also match. This establishes the source correspondence for the selected
layout; it does not pretend that the newer Git revision produced the old GDS.
The [machine-readable selection](../evidence/v2-release-selection/selection.json)
records the hashes and comparison method. Historical manifests retain their
original `release_acceptance: false` reporting scope. The existing same-source
acceptance checker is unchanged; this explicitly recorded release selection
does not constitute a successful execution of that checker for `881495c`.

The selected GDS SHA-256 is
`902c78fce4a69fde56dbf209b34d5c809e2d44610e6b1e790f03963bb97322c6`;
the netlist SHA-256 is
`d99cc8992fe8fc71a5a7dc5c0fd40f162a5f84a1e2fc9cf7a1166c069bda1a84`.
The current physical checker was rerun successfully against those retained
results and exact required test identities. Minimum extracted setup slack is
18.456291 ns; minimum hold slack is 0.054473 ns across the three corners.

Executable migration measurements preserve the same production executed lines
and branch arcs: 2948/4745 lines and 1197/1912 branches at the Step 1 checkpoint.
The [frozen migration record](https://github.com/mcranny/protocol-emulator-asic/tree/447b3dfd0986d473c71afa28efafb3a5cff48da7/evidence/test-migration-3d5210a)
accounts for every baseline pytest case and cocotb scenario. Those measurements
are not a claim of complete RTL or instruction-path coverage.

## Release assembly and publication checklist

- Retain the original recovered physical source, prepared inputs, checkpoint,
  pinned tools/PDK, GDS/netlist, full geometry and every required passing check.
- Retain both hosted timeouts and the failed parent route; the successful
  recovery and later selection supplement those records.
- Bind the `2.0.0rc1` software archives and their complete functional evidence
  to `881495c`, with the exact source correspondence recorded above.
- Run the current full V2 gate-level pin suite and four-state reset/state-loss
  audit against the selected netlist, recording the test revision separately.
- Review the release-selection documentation, pass the complete functional gate
  and strict HTML build after those edits, then authorize its commit and gated push.
- Assemble source/package archives, updated Sphinx HTML, example programs/traces,
  checksums, release notes and an index naming both physical and software sources.
- Obtain separate authorization for merging, tagging, release publication and
  competition submission. Verify the recipient's acceptance of the documented
  local checkpoint procedure before claiming competition acceptance.

No further route is required by the owner's release selection. Future designs
or physical-setting changes still require corresponding physical qualification;
the selected evidence cannot be transferred to a changed implementation.
