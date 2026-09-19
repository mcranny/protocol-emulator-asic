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
