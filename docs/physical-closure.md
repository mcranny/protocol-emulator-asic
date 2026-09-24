# Electrical closure experiments

The acceptance limits remain 25 MHz, 6x4 tiles, nonnegative setup/hold slack,
and zero slew, capacitance, fanout, geometry, and LVS violations. Changes here
repair the implementation; they do not relax library constraints or suppress
reported violations. The [validation record](validation.md) remains the source
of measured signoff evidence.

## Starting point

Physical run 35414846616 at b8493a4 reported 55 slow-corner slew violations and
123 fanout violations. The clock tree used 160 sg13cmos5l_buf_8 buffers; many
leaf buffers drove 16-19 loads despite the library limit of 8. A few non-clock
nets also exceeded fanout. Worst slow-corner slew was 3.282584 ns against a
2.507400 ns limit.

The resolved PNR_CORNERS value was null. In pinned LibreLane 3.1.0.dev3,
OpenROADStep.prepare_env falls back to DEFAULT_CORNER alone, which was typical.
Thus the repair passes did not optimize the slow corner that failed signoff.
Post-global-routing design repair and timing repair were both disabled, and
the default slew/capacitance checker corner filters matched no corners.

## First repair configuration

- Explicitly optimize at slow, fast, and typical signoff corners.
- Limit sink clusters to six loads, leaving room below the library limit of
  eight for additional clock-tree loads.
- Enable post-global-routing design repair with 20% slew/capacitance margins.
- Enable post-global-routing timing repair to recover setup/hold after changes.
- Make slew and capacitance checks fatal at every corner; the repository's
  acceptance checker additionally enforces fanout and missing-metric failures.

These controls are implemented in the pinned flow's
[OpenROAD steps](https://github.com/librelane/librelane/blob/ca6adb1e2982cd75445a68b632d461213d8ca421/librelane/steps/openroad.py),
[CTS script](https://github.com/librelane/librelane/blob/ca6adb1e2982cd75445a68b632d461213d8ca421/librelane/scripts/openroad/cts.tcl), and
[routed design repair](https://github.com/librelane/librelane/blob/ca6adb1e2982cd75445a68b632d461213d8ca421/librelane/scripts/openroad/repair_design_postgrt.tcl).

This configuration is a closure candidate, not proof of closure. Accept only
the resulting routed metrics and matching full-workflow regression artifacts.
In particular, recheck final fanout after antenna-diode insertion, clock-tree
changes, and hold repair; an intermediate report is not sufficient.

## First candidate results and second repair

Run [35429649064](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35429649064)
at f3e605e completed routing and failed the newly enforced slow-corner slew
gate. Final fanout violations fell from 123 to 3; no clock-tree leaf buffers
remain on the fanout violator list. Slew violations remain at 55, with worst
slew 3.320069 ns. Standard-cell area is 241308 um2 (26.7402% utilization).
Worst setup/hold slacks are 21.167381 / 0.098810 ns; geometry, LVS, capacitance,
and final antenna violation counts are zero. No precheck or gate-level job
ran because the physical build failed.

The three remaining fanout drivers are data nets with added antenna diodes:
_12267_/Y drives nine diodes plus three other loads, _13010_/Y drives six
diodes plus three other loads, and fanout1125/X drives three diodes plus six
other loads. The standard flow repairs design rules before antenna insertion,
so those extra loads were not present during its final design-rule repair.

The next configuration adds a second routed design-repair pass immediately
after antenna repair, before the existing timing-repair and detailed-routing
steps. It also sets a conservative 1.5 ns transition constraint below the
2.5074 ns slow-corner library limit: the first routed repair detected only one
slew violation and resized no cells, despite the later slow-corner failures.
An explicit tighter optimization constraint makes that margin visible to the
repair engine. No signoff limit is increased or filtered out. Both the
original and added repair passes remain enabled, and all final electrical,
antenna, geometry, timing, precheck, and gate-level gates must still pass.

## Second candidate and local repair diagnosis

Run [35460188674](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35460188674)
at c22a3d4 failed the slow and typical slew gates. Final counts were 125 slow,
23 typical, and zero fast slew violations under the tighter 1.5 ns constraint,
plus one fanout violation per corner. Standard-cell area was 241531 um2
(26.7649%). Worst setup/hold slack remained +21.090696 / +0.100768 ns.
Precheck, gate-level CI, and acceptance were skipped.

A local native ARM64 run of the exact OpenROAD revision dcf36133 reproduced
the extracted slow-corner slew report from the archived database, SDC, and
SPEF. The archived resizer_values_after.rpt showed zero layer/via RC entries
at every corner, despite nonzero values in tlef_values.rpt. The pinned flow's
default set_rc path does not initialize those tables when LAYERS_RC is empty.

A controlled probe of the post-antenna database with explicit technology-LEF
layer/via values inserted 23 buffers in 24 nets, versus four buffers in the
archived repair pass. Its immediate estimated slow-corner slew/fanout report
was clean. This proves a repair-path improvement, not extracted signoff:
detailed routing, antenna repair, and extraction can introduce later violations.

The third configuration explicitly populates LAYERS_RC for all nominal-RC
corners and VIAS_R using the pinned technology-LEF values. Units are kohm/um,
pF/um, and kohm respectively. It retains the 1.5 ns constraint, all signoff
corners, 25 MHz, and 6x4. A regression test freezes these values. Final routed
electrical metrics, precheck, and matching gate-level tests are still required.

## Third candidate local routed result

The native ARM64 local route/extraction/STA run used the exact physical inputs
in 3056a35 (captured before commit, so the manifest records c22a3d4 plus dirty
input hashes). It finished with zero fanout, capacitance, routing-DRC, and
antenna violations. Slew counts at 1.5 ns are 0 fast, 90 slow, and 0 typical;
worst slow-corner slew is 2.297081 ns. Area is 242867 um2 (26.9129%), with
worst setup/hold slack +21.217341 / +0.106257 ns.

This is an improvement, not acceptance under the configured 1.5 ns target.
The library default is 2.5074 ns, but the current stricter target remains in
force. Do not silently waive it. Final layout DRC/LVS, precheck, and matching
gate-level tests were not included in this shortened local run.

Resume from this draft, using the local runner on main. Inspect the slow-corner
drivers and route-vs-extraction load estimates before choosing the next repair.
Keep the source/input hashes with each experiment and require a complete
same-revision acceptance run before release.

## Extracted-load diagnosis

Read-only probes of the saved routed database reproduce the 90 violating pins
on 27 distinct nets. The worst nets have low fanout but long wires driven by
weak complex gates. Re-estimating global-route parasitics on the same database
substantially understates their final extracted load:

| Driver | Estimated load / slew | Extracted load / slew |
| --- | --- | --- |
| `_09426_/Y` | 0.075443 pF / 1.152347 ns | 0.146151 pF / 2.177641 ns |
| `_09301_/Y` | 0.052732 pF / 0.970481 ns | 0.113211 pF / 2.026532 ns |

The worst driver, `_09159_/Y`, drives one receiver roughly 430 um away. Its
estimated slew is 1.167992 ns, just below the previous repair threshold of
1.2 ns (20% margin on 1.5 ns), while extracted slew reaches 2.296370 ns.
Positive path slack and legal fanout therefore do not explain away the failure.

The next candidate increases the global-route repair slew margin to 55%,
giving a 0.675 ns optimization threshold to accommodate the observed load
underestimate. The nominal RC values and the final 1.5 ns signoff target remain
unchanged. The release checker also explicitly rejects an absent or changed
transition target, even when reported violation counts are zero. Only a new
extracted run and complete matching acceptance can validate this candidate.

The resulting local route/extraction/STA checkpoint passed: zero slew, fanout,
capacitance, routing-DRC, antenna, and unannotated-signal violations at all
required corners. Worst setup/hold slack is +21.053364 / +0.106182 ns; cell
area is 244113 um2 (27.0510%). Full same-revision release acceptance remains
mandatory; see the [validation record](validation.md).
