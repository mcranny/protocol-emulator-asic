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
