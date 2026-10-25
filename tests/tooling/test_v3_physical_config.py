import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG = json.loads((ROOT / "physical/v3/config.json").read_text())
SRAM = "RM_IHPSG13_1P_256x32_c2_bm_bist"
MACRO_W, MACRO_H = 416.64, 118.78
# Left edge of the first placement site in the Tiny Tapeout CMOS5L tile templates;
# pdngen measures the offset of the first stripe's centre from it.
CORE_X0 = 2.88
CORNERS = {"nom_slow_1p08V_125C", "nom_typ_1p20V_25C", "nom_fast_1p32V_m40C"}


def instances():
    return CONFIG["MACROS"][SRAM]["instances"]


def footprint(inst):
    x, y = inst["location"]
    return x, y, x + MACRO_W, y + MACRO_H


def pdn_columns():
    text = (ROOT / "physical/v3/pdn-columns.txt").read_text()
    columns = {}
    for net in ("VDD!", "VDDARRAY!", "VSS!"):
        match = re.search(rf"^{re.escape(net)} full: \[([^\]]+)\]", text, re.M)
        columns[net] = [float(v) for v in match.group(1).split(",")]
    return columns


def close(a, b):
    return math.isclose(a, b, abs_tol=1e-6)


def test_timing_and_check_settings():
    assert CONFIG["CLOCK_PERIOD"] == 20.833
    assert CONFIG["MAX_TRANSITION_CONSTRAINT"] == 1.5
    assert CONFIG["PL_RESIZER_HOLD_SLACK_MARGIN"] == 0.10
    assert CONFIG["GRT_RESIZER_HOLD_SLACK_MARGIN"] == 0.10
    assert CONFIG["RUN_MAGIC_DRC"] == 0
    assert CONFIG["RUN_KLAYOUT_DRC"] == 1
    assert CONFIG["ERROR_ON_ILLEGAL_OVERLAPS"] == 0


def test_project_targets_8x4_at_48_mhz():
    info = (ROOT / "info.yaml").read_text()
    assert re.search(r'^\s+tiles: "8x4"$', info, re.M)
    assert re.search(r"^\s+clock_hz: 48000000$", info, re.M)


def test_synthesis_instantiates_the_macro():
    assert "V3_SRAM_BLACKBOX" in CONFIG["VERILOG_DEFINES"]


def side_by_side():
    return sorted(instances().values(), key=lambda i: i["location"][0])


def test_two_macros_side_by_side_on_the_bottom_edge_with_pins_up():
    insts = instances()
    assert set(insts) == {"u_progmem0.u_sram", "u_progmem1.u_sram"}
    left, right = side_by_side()
    # The LEF puts every signal pin on the bottom edge, so both macros are
    # mirrored to face their pins up into the core. Nothing is placed below
    # or between them, so no net has to cross a macro: stacked on the left
    # edge, each macro walled the logic under the top-edge I/O pins off from
    # the logic below it, and global routing ran 112 nets through them.
    assert left["orientation"] == right["orientation"] == "FS"
    assert left["location"][0] == CORE_X0
    assert left["location"][1] == right["location"][1]
    # The second macro keeps the supply-column phase of the first.
    offset = right["location"][0] - left["location"][0]
    assert offset > MACRO_W
    assert close(offset % CONFIG["FP_PDN_VPITCH"], 0) or close(offset % CONFIG["FP_PDN_VPITCH"], CONFIG["FP_PDN_VPITCH"])
    # Every row below the macros overlaps the halo, so none is left for cells.
    bottom = left["location"][1]
    assert bottom - CONFIG["FP_MACRO_VERTICAL_HALO"] < 3.78 + 3.78
    # The power script restores the row just below the macros, and its 0.44 um
    # Metal1 rail runs along the row's top edge. The SRAM layout has Metal1
    # 0.285 um inside its edge, so the macro must clear the rail by the 0.18 um
    # Metal1 spacing.
    rail = 3.78 + math.floor((bottom - 3.78) / 3.78 + 1e-9) * 3.78
    assert bottom - (rail + 0.22) >= 0.18


def test_each_macro_connects_both_supplies():
    for name in instances():
        hooks = [h.split() for h in CONFIG["PDN_MACRO_CONNECTIONS"] if h.split()[0] == name]
        assert len(hooks) == 2
        assert {h[3] for h in hooks} == {"VDD!", "VDDARRAY!"}
        assert all(h[1:3] == ["VPWR", "VGND"] and h[4] == "VSS!" for h in hooks)


def test_liberty_corners_mapped():
    libs = CONFIG["MACROS"][SRAM]["lib"]
    assert set(libs) == CORNERS == set(CONFIG["PNR_CORNERS"])
    assert libs["nom_fast_1p32V_m40C"][0].endswith("_fast_1p32V_m55C.lib")
    assert libs["nom_slow_1p08V_125C"][0].endswith("_slow_1p08V_125C.lib")
    assert libs["nom_typ_1p20V_25C"][0].endswith("_typ_1p20V_25C.lib")


def test_placement_halo_leaves_rows_beside_the_pins():
    # Each macro has 186 signal pins and 110 of them are tied off. Their
    # tie cells and output buffers have to sit near the pins: an 80/50 um halo
    # pushed every one of those cells 80 um away from the pins and left 41
    # SRAM outputs over their capacitance limit.
    assert 0 < CONFIG["FP_MACRO_VERTICAL_HALO"] <= 10
    assert 0 < CONFIG["FP_MACRO_HORIZONTAL_HALO"] <= 20
    # The two side halos together cover the gap between the macros. A strip
    # of rows left there is a pocket that gets its own short power stripes.
    left, right = side_by_side()
    assert 2 * CONFIG["FP_MACRO_HORIZONTAL_HALO"] >= right["location"][0] - footprint(left)[2]


def test_long_nets_get_repeatered_before_routing_commits():
    # Left disabled, a direct ~319 um run from u_progmem1.u_sram/A_DOUT[3] to
    # its first load left that pin at 0.087 pF against its 0.064 pF limit,
    # since nothing split the wire's own capacitance off the driver. The
    # break-even for that pin is near 190-235 um, so the cap must sit at or
    # below that range, and nonzero. 230 reintroduced a (different) violation
    # and took the fast corner's r2r setup slack negative, so looser isn't a
    # free improvement; 190 is the tightest and only value confirmed clean.
    assert 0 < CONFIG["DESIGN_REPAIR_MAX_WIRE_LENGTH"] <= 190
    # Applying the same cap after global routing instead forces ~22,500 extra
    # buffers into an already-routed design across its repair passes and
    # overflowed routing; this must stay disabled there.
    assert CONFIG["GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"] == 0


def test_wire_capacitance_estimate_matches_extraction():
    # Repair and timing optimization before routing see only these per-layer
    # values. With the earlier ones, extracted net capacitance came out 1.29x
    # their estimate (capacitance-weighted, same placement), so repair never
    # saw the paths it had to fix.
    earlier = {"Metal1": 6.8784e-05, "Metal2": 9.302e-05, "Metal3": 9.2e-05, "Metal4": 9.1788e-05}
    layers = CONFIG["LAYERS_RC"]["nom_*"]
    assert set(layers) == set(earlier)
    for layer, cap in earlier.items():
        assert math.isclose(layers[layer]["cap"] / cap, 1.29, abs_tol=1e-3), layer


def test_timing_repair_targets_the_signoff_setup_margin():
    # No timing repair runs after detailed routing, and extraction took the
    # slow-corner register-to-register slack from -0.28 ns estimated to
    # -2.43 ns, so repair before routing aims at the +2 ns signoff margin
    # rather than the 0.05/0.025 ns defaults.
    assert CONFIG["PL_RESIZER_SETUP_SLACK_MARGIN"] == 2.0
    assert CONFIG["GRT_RESIZER_SETUP_SLACK_MARGIN"] == 2.0
    # Without timing-driven placement the worst SRAM-launched path wandered
    # about 5,600 um across the die; with it, pre-route slow slack rose from
    # +1.79 to +2.18 ns at the same margins.
    assert CONFIG["PL_TIMING_DRIVEN"] is True


def test_global_routing_must_not_overflow_into_the_macros():
    # With congestion allowed, global routing left guides for 351 nets inside
    # the SRAM blockages and detailed routing could not clear them.
    assert CONFIG["GRT_ALLOW_CONGESTION"] == 0
    # With the macros on the bottom edge, 0.3 overflowed and 0.15 cut the
    # slow-corner setup slack to 0.20 ns; two runs at 0.2 kept 0.31 to 0.87 ns.
    assert CONFIG["GRT_ADJUSTMENT"] == 0.2


def stripes():
    pitch, width = CONFIG["FP_PDN_VPITCH"], CONFIG["FP_PDN_VWIDTH"]
    first = CORE_X0 + CONFIG["FP_PDN_VOFFSET"] - width / 2
    for k in range(int(1724.16 // pitch) + 1):
        vdd = first + k * pitch
        yield vdd, vdd + width + CONFIG["FP_PDN_VSPACING"]


def pdn_blocked(x0, x1, y0, y1):
    return any(
        layer == "Metal4" and ox0 <= x0 and x1 <= ox1 and oy0 <= y0 and y1 <= oy1
        for layer, ox0, oy0, ox1, oy1 in CONFIG["PDN_OBSTRUCTIONS"]
    )


def test_power_stripes_land_on_macro_supply_columns():
    columns = pdn_columns()
    width = CONFIG["FP_PDN_VWIDTH"]
    assert CONFIG["FP_PDN_VPITCH"] != 50.0
    assert close(width, 2.81)
    assert close(CONFIG["FP_PDN_VPITCH"] % 11.24, 0) or close(CONFIG["FP_PDN_VPITCH"] % 11.24, 11.24)
    for inst in instances().values():
        mx0, my0, mx1, my1 = footprint(inst)
        landed = {"VDD": 0, "VSS": 0}
        for vdd, vss in stripes():
            for kind, left, nets in (("VDD", vdd, ("VDD!", "VDDARRAY!")), ("VSS", vss, ("VSS!",))):
                if left + width <= mx0 or left >= mx1:
                    continue
                if pdn_blocked(left, left + width, my0 - 2, my1 + 2):
                    continue
                local = left - mx0
                assert any(close(local, c) for net in nets for c in columns[net]), (kind, local)
                landed[kind] += 1
        assert landed["VDD"] >= 4 and landed["VSS"] >= 4


def test_power_grid_script_bridges_stripes_onto_macros():
    assert CONFIG["PDN_CFG"] == "dir::pdn_cfg.tcl"
    script = (ROOT / "physical/v3/pdn_cfg.tcl").read_text()
    assert "rename pdngen v3_pdngen" in script
    assert "v3_bridge_macro_columns" in script
    restore = script.split("proc v3_restore_halo_rows", 1)[1].split("proc ", 1)[0]
    # A halo row left out would leave the stripes at that macro edge unstrapped.
    assert "cannot be extended" in restore
    assert "more than one segment" in restore
    body = script.split("proc pdngen", 1)[1]
    assert body.index("v3_restore_halo_rows") < body.index("v3_pdngen")
    assert body.index("v3_bridge_macro_columns") < body.index("v3_remove_halo_rows")


def test_routing_obstructions_cover_each_macro_on_metal2_to_metal4():
    for inst in instances().values():
        mx0, my0, mx1, my1 = footprint(inst)
        for layer in ("Metal2", "Metal3", "Metal4"):
            hits = [
                o for o in CONFIG["ROUTING_OBSTRUCTIONS"]
                if o[0] == layer and close(o[1], mx0) and close(o[3], mx1)
                and my0 <= o[2] < o[4] <= my1
            ]
            assert len(hits) == 1, (layer, inst)
            _, _, oy0, _, oy1 = hits[0]
            # Only a strip along the pin edge stays open for pin access.
            pin_edge_gap = (my1 - oy1) if inst["orientation"] == "FS" else (oy0 - my0)
            assert 0 < pin_edge_gap <= 1.0 + 1e-6
            assert close(oy0, my0) if inst["orientation"] == "FS" else close(oy1, my1)


def test_no_preplaced_diodes_and_power_obstructions_stay_by_the_macros():
    assert CONFIG["INSERT_ECO_DIODES"] == []
    assert CONFIG["DIODE_CELL"] == "sg13cmos5l_antennanp/A"
    # Antenna repair before detailed routing (and the one-time diode pass
    # substituted for it) stays on. Detailed routing still left 12 Metal3
    # antenna violations, so it gets exactly one diode-only repair pass of its
    # own. Each pass re-runs detailed routing, and on a congested earlier
    # floorplan a longer loop re-ran a full 64-iteration route and nearly
    # doubled the marker count.
    assert CONFIG["GRT_ANTENNA_REPAIR_ITERS"] >= 1
    assert CONFIG["DRT_ANTENNA_REPAIR_ITERS"] == 1
    assert CONFIG["DRT_ANTENNA_REPAIR_DIODE_ONLY"] is True
    assert CONFIG["meta"]["substituting_steps"]["+OpenROAD.GlobalRouting"] == "Odb.InsertECODiodes"
    # One obstruction per macro, over its off-phase half, ending 4 um above
    # and below it so the in-phase stripes still reach both ends of the columns.
    obstructions = sorted(CONFIG["PDN_OBSTRUCTIONS"], key=lambda o: o[1])
    assert len(obstructions) == 2
    for inst, (layer, ox0, oy0, ox1, oy1) in zip(side_by_side(), obstructions):
        mx0, my0, mx1, my1 = footprint(inst)
        assert layer == "Metal4"
        assert mx0 + MACRO_W / 2 - 30 < ox0 < mx1 < ox1 < mx1 + 4
        assert close(my0 - oy0, 4) and close(oy1 - my1, 4)
