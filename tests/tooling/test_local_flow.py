from pathlib import Path

import pytest

from tools import local_flow as flow


@pytest.fixture
def make_project():
    def create():
        return {
            "language": "Verilog",
            "tiles": "6x4",
            "clock_hz": 25_000_000,
            "top_module": "tt_um_mcranny_protocol_emulator",
            "source_files": ["project.v", "engine.v", "host_spi.v"],
        }

    return create


def test_config_matches_pinned_support_tools(make_project):
    # Exact user_config.json from run 35460188674, produced by support-tools f6bf5c5.
    assert flow.user_config(make_project(), {"6x4": "0 0 1289.28 710.64"}) == {
        "DESIGN_NAME": "tt_um_mcranny_protocol_emulator",
        "VERILOG_FILES": ["dir::project.v", "dir::engine.v", "dir::host_spi.v"],
        "DIE_AREA": "0 0 1289.28 710.64",
        "FP_DEF_TEMPLATE": "dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def",
        "VDD_PIN": "VPWR",
        "GND_PIN": "VGND",
        "RT_MAX_LAYER": "Metal4",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("language", "VHDL"),
        ("tiles", "8x4"),
        ("clock_hz", 50_000_000),
        ("source_files", []),
        ("source_files", ["../secret.v"]),
        ("source_files", ["/secret.v"]),
        ("source_files", ["engine.txt"]),
    ],
    ids=[
        "key,value=language-VHDL",
        "key,value=tiles-8x4",
        "key,value=clock_hz-50000000",
        "key,value=source_files-value3",
        "key,value=source_files-value4",
        "key,value=source_files-value5",
        "key,value=source_files-value6",
    ],
)
def test_config_rejects_unsupported_inputs(make_project, key, value):
    data = make_project()
    data[key] = value
    with pytest.raises(ValueError):
        flow.user_config(data, {"6x4": "0 0 1289.28 710.64"})


@pytest.mark.parametrize(
    "revision,dirty",
    [("wrong", ""), (flow.PDK_REV, " M config.tcl")],
    ids=[
        "revision,dirty=wrong-",
        "revision,dirty=2bbec755dc67ca3db0261c3d6163e15735d66710- M config.tcl",
    ],
)
def test_dependency_must_match_clean_pinned_revision(monkeypatch, revision, dirty):
    monkeypatch.setattr(
        flow,
        "output",
        lambda *args, **kwargs: revision if "rev-parse" in args else dirty,
    )
    with pytest.raises(ValueError):
        flow.verify_checkout(Path("unused"), flow.PDK_REV)


@pytest.fixture
def make_routed_metrics(tmp_path):
    def create():
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

    return create


def test_routed_diagnostic_accepts_complete_clean_metrics(
    make_routed_metrics, tmp_path
):
    make_routed_metrics()
    assert flow.route_issues(tmp_path) == []


@pytest.mark.parametrize(
    "bad",
    [None, True, "0", 1, -1, float("nan"), float("inf")],
    ids=["bad=None", "bad=True", "bad=0", "bad=1", "bad=-1", "bad=nan", "bad=inf"],
)
def test_routed_diagnostic_rejects_invalid_electrical_metrics(
    make_routed_metrics, tmp_path, bad
):
    import json

    path, metrics = make_routed_metrics()
    metrics["design__max_slew_violation__count__corner:nom_slow_1p08V_125C"] = bad
    path.write_text(json.dumps(metrics))
    assert flow.route_issues(tmp_path)


def test_routed_diagnostic_rejects_missing_file(tmp_path):
    assert flow.route_issues(tmp_path)


@pytest.mark.parametrize(
    "key",
    [
        "timing__hold__ws__corner:nom_fast_1p32V_m40C",
        "timing__setup_r2r__ws__corner:nom_slow_1p08V_125C",
        "route__drc_errors",
    ],
    ids=[
        "key=timing__hold__ws__corner:nom_fast_1p32V_m40C",
        "key=timing__setup_r2r__ws__corner:nom_slow_1p08V_125C",
        "key=route__drc_errors",
    ],
)
def test_routed_diagnostic_rejects_missing_required_metrics(
    make_routed_metrics, tmp_path, key
):
    import json

    path, metrics = make_routed_metrics()
    del metrics[key]
    path.write_text(json.dumps(metrics))
    assert flow.route_issues(tmp_path)


def test_inside_sets_tool_threads_and_stops_route_before_layout_checks(
    make_project, tmp_path, monkeypatch
):
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
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(
            safe_load=lambda text: {"project": make_project()}
            if text == "project"
            else {"6x4": "0 0 1289.28 710.64"}
        ),
    )
    monkeypatch.setattr(flow, "output", lambda *args: "tool-version")
    calls = []
    monkeypatch.setattr(flow.subprocess, "call", lambda args: calls.append(args) or 0)
    assert flow.inside("route", 4) == 0
    config = json.loads((tmp_path / "src/config_merged.json").read_text())
    assert config["OPENROAD_THREADS"] == config["STA_THREADS"] == 4
    assert config["CLOCK_PERIOD"] == 40
    assert calls[0][calls[0].index("--to") + 1] == "OpenROAD.STAPostPNR"


V3_SOURCES = [
    "v3_timebase.v", "v3_pin_cell.v", "v3_input_filter.v", "v3_progmem.v", "v3_crc.v",
    "v3_fifo.v", "v3_engine.v", "v3_host_spi.v", "v3_capture.v", "v3_project.v",
]
V3_DIE = "0 0 1724.16 710.64"


@pytest.fixture
def make_v3_project():
    def create():
        return {
            "language": "Verilog",
            "tiles": "8x4",
            "clock_hz": 48_000_000,
            "top_module": "tt_um_mcranny_protocol_emulator_v3",
            "source_files": list(V3_SOURCES),
        }

    return create


def test_v3_fixture_matches_info_yaml(make_v3_project):
    text = (Path(__file__).resolve().parents[2] / "info.yaml").read_text()
    project = make_v3_project()
    assert f'tiles: "{project["tiles"]}"' in text
    assert f'top_module: "{project["top_module"]}"' in text
    assert f"clock_hz: {project['clock_hz']}" in text
    for source in project["source_files"]:
        assert f'- "{source}"' in text


def test_v3_config_addresses_shared_trees_from_the_physical_directory(make_v3_project):
    assert flow.user_config(make_v3_project(), {"8x4": V3_DIE}) == {
        "DESIGN_NAME": "tt_um_mcranny_protocol_emulator_v3",
        "VERILOG_FILES": [f"dir::../../src/{s}" for s in V3_SOURCES],
        "DIE_AREA": V3_DIE,
        "FP_DEF_TEMPLATE": "dir::../../tt/tech/ihp-sg13cmos5l/def/tt_block_8x4_pgvdd.def",
        "VDD_PIN": "VPWR",
        "GND_PIN": "VGND",
        "RT_MAX_LAYER": "Metal4",
    }


@pytest.mark.parametrize(
    "key,value",
    [
        ("tiles", "6x4"),
        ("tiles", "8x2"),
        ("clock_hz", 25_000_000),
        ("clock_hz", 50_000_000),
        ("top_module", "tt_um_mcranny_protocol_emulator"),
        ("language", "VHDL"),
        ("source_files", []),
        ("source_files", ["../v3_project.v"]),
        ("source_files", ["v3_opcodes.vh"]),
    ],
)
def test_v3_config_rejects_unsupported_inputs(make_v3_project, key, value):
    data = make_v3_project()
    data[key] = value
    with pytest.raises(ValueError):
        flow.user_config(data, {"6x4": "0 0 1289.28 710.64", "8x4": V3_DIE})


def _stage_inside(tmp_path, monkeypatch, project, sizes, files):
    import sys
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    (tmp_path / "info.yaml").write_text("project")
    sizes_path = tmp_path / "tt/tech/ihp-sg13cmos5l/tile_sizes.yaml"
    sizes_path.parent.mkdir(parents=True)
    sizes_path.write_text("tiles")
    monkeypatch.setitem(
        sys.modules,
        "yaml",
        SimpleNamespace(safe_load=lambda text: {"project": project} if text == "project" else sizes),
    )
    monkeypatch.setattr(flow, "output", lambda *args: "tool-version")
    calls = []
    monkeypatch.setattr(flow.subprocess, "call", lambda args: calls.append(args) or 0)
    return calls


def test_inside_merges_the_v3_config_next_to_it_at_48_mhz(make_v3_project, tmp_path, monkeypatch):
    import json

    calls = _stage_inside(tmp_path, monkeypatch, make_v3_project(), {"8x4": V3_DIE}, {
        "physical/v3/config.json": '{"CLOCK_PERIOD": 20.833, "PDN_CFG": "dir::pdn_cfg.tcl"}',
        "physical/v3/pdn_cfg.tcl": "# pdn",
    })
    assert flow.inside("full", 6) == 0
    config = json.loads((tmp_path / "physical/v3/config_merged.json").read_text())
    assert config["CLOCK_PERIOD"] == 20.833
    assert config["PDN_CFG"] == "dir::pdn_cfg.tcl"
    assert config["OPENROAD_THREADS"] == config["STA_THREADS"] == 6
    assert config["DIE_AREA"] == V3_DIE
    assert not (tmp_path / "src/config_merged.json").exists()
    assert calls[0][-1] == "physical/v3/config_merged.json"
    assert "--to" not in calls[0], "a full run must not stop at routing"
    assert calls[0][2] == "--librelane", "LibreLane must run through the LVS report repair"


@pytest.mark.parametrize(
    "stage,last_step",
    [("pdn", "OpenROAD.GeneratePDN"), ("grt", "OpenROAD.STAMidPNR-3")],
)
def test_inside_stops_shortened_stages(make_v3_project, tmp_path, monkeypatch, stage, last_step):
    calls = _stage_inside(tmp_path, monkeypatch, make_v3_project(), {"8x4": V3_DIE}, {
        "physical/v3/config.json": '{"CLOCK_PERIOD": 20.833}',
        "physical/v3/pdn_cfg.tcl": "# pdn",
    })
    assert flow.inside(stage, 4) == 0
    assert calls[0][calls[0].index("--to") + 1] == last_step


def test_failed_full_run_still_reports_its_metrics(make_routed_metrics, tmp_path):
    import json

    path, metrics = make_routed_metrics()
    metrics["route__drc_errors"] = 369
    path.write_text(json.dumps(metrics))
    assert flow.run_issues("full", 1, tmp_path) == [
        "physical tool execution failed", "route__drc_errors = 369"]
    assert flow.run_issues("full", 0, tmp_path) == ["route__drc_errors = 369"]


@pytest.mark.parametrize("stage", ["pdn", "grt"])
def test_shortened_stages_are_not_judged_on_routed_metrics(tmp_path, stage):
    assert flow.run_issues(stage, 0, tmp_path) == []
    assert flow.run_issues(stage, 1, tmp_path) == ["physical tool execution failed"]


def test_modified_files_names_copied_files_that_differ_from_the_commit(tmp_path):
    import subprocess

    def git(*args):
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q")
    for name in ("same.v", "changed.tcl", "ignored.v"):
        (tmp_path / name).write_text("committed\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    (tmp_path / "changed.tcl").write_text("edited\n")
    (tmp_path / "ignored.v").write_text("edited\n")
    (tmp_path / "new.v").write_text("untracked\n")
    copied = ["same.v", "changed.tcl", "new.v"]
    assert flow.modified_files(tmp_path, copied) == ["changed.tcl", "new.v"]


@pytest.mark.parametrize(
    "files",
    [
        {"physical/v3/config.json": '{"CLOCK_PERIOD": 40}', "physical/v3/pdn_cfg.tcl": ""},
        {"physical/v3/config.json": '{"CLOCK_PERIOD": 20.833}'},
    ],
    ids=["v1-period", "no-pdn-cfg"],
)
def test_inside_refuses_a_v3_config_with_the_wrong_period_or_no_pdn_script(
    make_v3_project, tmp_path, monkeypatch, files
):
    calls = _stage_inside(tmp_path, monkeypatch, make_v3_project(), {"8x4": V3_DIE}, files)
    with pytest.raises(ValueError):
        flow.inside("full", 4)
    assert calls == []


def test_repair_json_escapes_doubles_only_bare_backslashes():
    import json

    # Netgen's report line for the SRAM supply pins, as written.
    line = '[ "RM_IHPSG13_1P_256x32_c2_bm_bist", "\\VDD!", 2 ]'
    with pytest.raises(json.JSONDecodeError):
        json.loads(line)
    assert json.loads(flow.repair_json_escapes(line))[1] == "\\VDD!"
    # Escapes Netgen already wrote correctly pass through unchanged.
    valid = '["\\\\VSS", "a\\"b", "\\u0041", "\\n"]'
    assert flow.repair_json_escapes(valid) == valid
    # A name that starts with u is not a unicode escape.
    assert json.loads(flow.repair_json_escapes('["\\u_engine1.r[0] "]')) == ["\\u_engine1.r[0] "]


def test_run_librelane_repairs_the_netgen_report_before_the_flow_runs(monkeypatch):
    import json
    import runpy
    import sys
    import types

    package = types.ModuleType("librelane")
    steps = types.ModuleType("librelane.steps")
    netgen = types.ModuleType("librelane.steps.netgen")
    netgen.json = json
    steps.netgen = netgen
    package.steps = steps
    for module in (package, steps, netgen):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    seen = {}

    def fake_run_module(name, run_name, alter_sys):
        seen["name"], seen["argv"] = name, list(sys.argv)
        seen["report"] = netgen.json.loads('{"pin": "\\VDD!"}')
        seen["dumps"] = netgen.json.dumps({"a": 1})
        raise SystemExit(3)

    monkeypatch.setattr(runpy, "run_module", fake_run_module)
    assert flow.run_librelane(["--run-tag", "local", "config.json"]) == 3
    assert seen == {
        "name": "librelane",
        "argv": ["librelane", "--run-tag", "local", "config.json"],
        "report": {"pin": "\\VDD!"},
        "dumps": '{"a": 1}',
    }
    assert netgen.json.JSONDecodeError is json.JSONDecodeError


def test_evidence_keys_use_posix_separators():
    relative = Path("src") / "config.json"
    assert flow.evidence_key(relative) == "src/config.json"
    assert "\\" not in flow.evidence_key(relative)
