import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("local_flow", Path(__file__).parents[1] / "tools/local_flow.py")
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)


def project():
    return {"language": "Verilog", "tiles": "6x4", "clock_hz": 25_000_000,
            "top_module": "tt_um_mcranny_protocol_emulator",
            "source_files": ["project.v", "engine.v", "host_spi.v"]}


def test_config_matches_pinned_support_tools():
    # Exact user_config.json from run 35460188674, produced by support-tools f6bf5c5.
    assert flow.user_config(project(), {"6x4": "0 0 1289.28 710.64"}) == {
        "DESIGN_NAME": "tt_um_mcranny_protocol_emulator",
        "VERILOG_FILES": ["dir::project.v", "dir::engine.v", "dir::host_spi.v"],
        "DIE_AREA": "0 0 1289.28 710.64",
        "FP_DEF_TEMPLATE": "dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def",
        "VDD_PIN": "VPWR", "GND_PIN": "VGND", "RT_MAX_LAYER": "Metal4",
    }


@pytest.mark.parametrize("key,value", [
    ("language", "VHDL"), ("tiles", "8x4"), ("clock_hz", 50_000_000),
    ("source_files", []), ("source_files", ["../secret.v"]),
    ("source_files", ["/secret.v"]), ("source_files", ["engine.txt"]),
])
def test_config_rejects_unsupported_inputs(key, value):
    data = project()
    data[key] = value
    with pytest.raises(ValueError):
        flow.user_config(data, {"6x4": "0 0 1289.28 710.64"})


@pytest.mark.parametrize("revision,dirty", [("wrong", ""), (flow.PDK_REV, " M config.tcl")])
def test_dependency_must_match_clean_pinned_revision(monkeypatch, revision, dirty):
    monkeypatch.setattr(flow, "output", lambda *args, **kwargs: revision if "rev-parse" in args else dirty)
    with pytest.raises(ValueError):
        flow.verify_checkout(Path("unused"), flow.PDK_REV)


def routed_metrics(tmp_path):
    import json
    metrics = {"route__drc_errors": 0, "route__antenna_violation__count": 0}
    corners = ("nom_fast_1p32V_m40C", "nom_slow_1p08V_125C", "nom_typ_1p20V_25C")
    for rule in ("slew", "fanout", "cap"):
        metrics[f"design__max_{rule}_violation__count"] = 0
        for corner in corners:
            metrics[f"design__max_{rule}_violation__count__corner:{corner}"] = 0
    for corner in corners:
        metrics[f"timing__unannotated_net_filtered__count__corner:{corner}"] = 0
        for kind in ("setup", "hold"):
            for suffix in ("", "_r2r"):
                metrics[f"timing__{kind}{suffix}__ws__corner:{corner}"] = 1
                metrics[f"timing__{kind}{suffix}_vio__count__corner:{corner}"] = 0
    path = tmp_path / "src/runs/local/final/metrics.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(metrics))
    return path, metrics


def test_routed_diagnostic_accepts_complete_clean_metrics(tmp_path):
    routed_metrics(tmp_path)
    assert flow.route_issues(tmp_path) == []


@pytest.mark.parametrize("bad", [None, True, "0", 1, -1, float("nan"), float("inf")])
def test_routed_diagnostic_rejects_invalid_electrical_metrics(tmp_path, bad):
    import json
    path, metrics = routed_metrics(tmp_path)
    metrics["design__max_slew_violation__count__corner:nom_slow_1p08V_125C"] = bad
    path.write_text(json.dumps(metrics))
    assert flow.route_issues(tmp_path)


def test_routed_diagnostic_rejects_missing_file(tmp_path):
    assert flow.route_issues(tmp_path)


@pytest.mark.parametrize("key", [
    "timing__hold__ws__corner:nom_fast_1p32V_m40C",
    "timing__setup_r2r__ws__corner:nom_slow_1p08V_125C",
    "route__drc_errors",
])
def test_routed_diagnostic_rejects_missing_required_metrics(tmp_path, key):
    import json
    path, metrics = routed_metrics(tmp_path)
    del metrics[key]
    path.write_text(json.dumps(metrics))
    assert flow.route_issues(tmp_path)


def test_inside_sets_tool_threads_and_stops_route_before_layout_checks(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src/config.json").write_text('{"CLOCK_PERIOD": 40}')
    (tmp_path / "info.yaml").write_text("project")
    sizes_path = tmp_path / "tt/tech/ihp-sg13cmos5l/tile_sizes.yaml"
    sizes_path.parent.mkdir(parents=True)
    sizes_path.write_text("tiles")
    monkeypatch.setitem(sys.modules, "yaml", SimpleNamespace(
        safe_load=lambda text: {"project": project()} if text == "project"
        else {"6x4": "0 0 1289.28 710.64"}))
    monkeypatch.setattr(flow, "output", lambda *args: "tool-version")
    calls = []
    monkeypatch.setattr(flow.subprocess, "call", lambda args: calls.append(args) or 0)
    assert flow.inside("route", 4) == 0
    config = json.loads((tmp_path / "src/config_merged.json").read_text())
    assert config["OPENROAD_THREADS"] == config["STA_THREADS"] == 4
    assert config["CLOCK_PERIOD"] == 40
    assert calls[0][calls[0].index("--to") + 1] == "OpenROAD.STAPostPNR"
