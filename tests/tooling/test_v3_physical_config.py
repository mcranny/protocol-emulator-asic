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


def test_two_macros_on_left_edge_with_pins_facing():
    insts = instances()
    assert set(insts) == {"u_progmem0.u_sram", "u_progmem1.u_sram"}
    lower, upper = sorted(insts.values(), key=lambda i: i["location"][1])
    assert lower["location"][0] == upper["location"][0] == CORE_X0
    # The LEF puts every signal pin on the bottom edge: the lower macro is
    # mirrored so its pins face up into the channel shared with the upper one.
    assert lower["orientation"] == "FS"
    assert upper["orientation"] == "N"
    # Sixteen standard-cell rows, so the two pin edges do not share a 40 um slot.
    gap = upper["location"][1] - footprint(lower)[3]
    assert gap >= 16 * 3.78
    assert close(upper["location"][1] % 3.78, 0)


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


def test_placement_halo():
    # The first routed pass at a 2 um halo put most of its DRC markers beside
    # and above the macros: 2,491 within 80 um of either side and another 358
    # above the upper macro. The halo has to clear both.
    assert CONFIG["FP_MACRO_HORIZONTAL_HALO"] == 80
    assert CONFIG["FP_MACRO_VERTICAL_HALO"] == 50
    lower, upper = sorted(instances().values(), key=lambda i: i["location"][1])
    gap = upper["location"][1] - footprint(lower)[3]
    # Each macro's vertical halo reaches into the channel from its own side;
    # together they have to clear nearly all of it so no cell sits on the
    # pin escape.
    assert 2 * CONFIG["FP_MACRO_VERTICAL_HALO"] >= gap - 1.0


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


def test_pin_channel_has_no_preplaced_diodes_or_power_obstruction():
    assert CONFIG["INSERT_ECO_DIODES"] == []
    assert CONFIG["DIODE_CELL"] == "sg13cmos5l_antennanp/A"
    # Antenna repair before detailed routing (and the one-time diode pass
    # substituted for it) stays on. The repair loop inside detailed routing
    # itself is off: on the previous run it re-ran a full 64-iteration route
    # after finding violations and nearly doubled the marker count.
    assert CONFIG["GRT_ANTENNA_REPAIR_ITERS"] >= 1
    assert CONFIG["DRT_ANTENNA_REPAIR_ITERS"] == 0
    assert CONFIG["meta"]["substituting_steps"]["+OpenROAD.GlobalRouting"] == "Odb.InsertECODiodes"
    lower, upper = sorted(instances().values(), key=lambda i: i["location"][1])
    gap_bottom = footprint(lower)[3]
    gap_top = upper["location"][1]
    mid_y = (gap_bottom + gap_top) / 2
    for x in (50.0, 300.0):
        assert not any(
            layer == "Metal4" and ox0 <= x <= ox1 and oy0 <= mid_y <= oy1
            for layer, ox0, oy0, ox1, oy1 in CONFIG["PDN_OBSTRUCTIONS"]
        )
