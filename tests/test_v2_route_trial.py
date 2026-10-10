"""Routing cannot consume failed/stale timing evidence or hide missing results."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import v2_closure as closure
from tools import v2_route_trial as trial
from tools.v2_route_geometry import inspect_block
from tools.v2_route_monitor import drc_summary, iterations, sample, stalled
from tools.v2_route_monitor import plateau_stop_reason
from tools.v2_route_flow import search_script


def corner_metrics(corner):
    metrics = {"closure__global_route_parasitics": 1, "closure__propagated_clock": 1}
    for rule in ("slew", "cap", "fanout"):
        metrics[f"design__max_{rule}_violation__count__corner:{corner}"] = 0
    for kind in ("setup", "hold"):
        for suffix in ("", "_r2r"):
            metrics[f"timing__{kind}{suffix}_vio__count__corner:{corner}"] = 0
            metrics[f"timing__{kind}{suffix}__ws__corner:{corner}"] = 0.05
    return metrics


@pytest.fixture
def timing(tmp_path):
    root = tmp_path / "timing-run"
    root.mkdir()
    closure.write(root / "config.json", closure.FIXED)
    closure.write(root / "state.json", {"odb": "/trial/test.odb", "metrics": {}})
    corners = {}
    for corner in closure.CORNERS:
        step = root / "timing" / corner / "1-openroad-stamidpnr"
        step.mkdir(parents=True)
        metrics = corner_metrics(corner)
        annotation = {"passed": True, "unannotated_drivers": ["unused"],
                      "unconnected_drivers": ["unused"], "unknown_drivers": [],
                      "connected_unannotated_drivers": [], "partially_unannotated_driver_count": 0}
        closure.write(step / "or_metrics_out.json", metrics)
        closure.write(step / "annotation.json", annotation)
        (step / "checks.rpt").write_text("retained report")
        corners[corner] = {"issues": [], "metrics": metrics, "annotation": annotation}
    closure.write(root / "timing-gate.json", {"passed": True, "corners": corners,
        "artifact_sha256": {str(p.relative_to(root)): closure.sha(p) for p in (root / "timing").rglob("*") if p.is_file()}})
    closure.write(root / "manifest.json", {"image": closure.IMAGE, "pdk_commit": closure.PDK_REV,
        "support_tools_commit": closure.TT_REV,
        "prepared_sha256": {name: closure.sha(root / name) for name in ("config.json", "state.json")}})
    return root


def test_timing_gate_accepts_exact_evidence(timing):
    _, config, state = trial.verify_timing(timing)
    assert config["RT_MIN_LAYER"] == "Metal2"
    assert state["metrics"] == {}


@pytest.mark.parametrize("change", ["missing_corner", "not_passed", "raw_metric", "unhashed_report", "config",
                                  "annotation", "dependency", "summary_metric"])
def test_route_rejects_missing_stale_or_failed_evidence(timing, change):
    gate = closure.read(timing / "timing-gate.json")
    corner = closure.CORNERS[0]
    if change == "missing_corner":
        del gate["corners"][corner]
    elif change == "not_passed":
        gate["passed"] = False
    elif change == "raw_metric":
        step = closure.timing_step(timing / "timing" / corner)
        closure.write(step / "or_metrics_out.json", corner_metrics(closure.CORNERS[1]))
    elif change == "unhashed_report":
        gate["artifact_sha256"] = {k: v for k, v in gate["artifact_sha256"].items() if not k.endswith("checks.rpt")}
    elif change == "config":
        config = closure.read(timing / "config.json")
        config["RT_MIN_LAYER"] = "Metal1"
        closure.write(timing / "config.json", config)
    elif change == "annotation":
        gate["corners"][corner]["annotation"]["connected_unannotated_drivers"] = ["data/X"]
    elif change == "summary_metric":
        gate["corners"][corner]["metrics"][f"timing__hold__ws__corner:{corner}"] = -1
    else:
        manifest = closure.read(timing / "manifest.json")
        manifest["image"] = "unrelated"
        closure.write(timing / "manifest.json", manifest)
    closure.write(timing / "timing-gate.json", gate)
    with pytest.raises(ValueError):
        trial.verify_timing(timing)


def test_monitor_distinguishes_completed_iterations_and_antenna_passes():
    log = """[INFO DRT-0195] Start 0th optimization iteration.
Completing 40% with 20 violations.
[INFO DRT-0199]   Number of violations = 10.
[INFO DRT-0195] Start 1st guides tiles iteration.
[INFO DRT-0199]   Number of violations = 0.
[INFO DRT-0195] Start 0th optimization iteration.
Completing 20% with 5 violations.
"""
    progress = iterations(log)
    assert [p["violations"] for p in progress["completed"]] == [10, 0]
    assert progress["current"] == {"run": 1, "iteration": 0, "kind": "optimization",
                                    "progress_percent": 20, "progress_violations": 5}


def test_monitor_uses_flushed_log_and_separate_observer_outputs(tmp_path):
    step = tmp_path / "runs/route/01-openroad-detailedrouting"
    step.mkdir(parents=True)
    start = "[INFO DRT-0195] Start 0th optimization iteration.\n"
    (step / "openroad-detailedrouting.log").write_text(start)
    (tmp_path / "flow.log").write_text(start + "[INFO DRT-0199]   Number of violations = 42.\n")
    _, progress = sample(tmp_path, tmp_path / "observer")
    assert progress["completed"][-1]["violations"] == 42
    assert progress["source_log"] == "flow.log"
    assert not (tmp_path / "route-progress.json").exists()
    assert (tmp_path / "observer/route-progress.json").exists()


def test_plateau_requires_nonzero_sustained_same_pass_counts():
    progress = {"completed": [{"run": 0, "iteration": i, "violations": 63} for i in range(40, 50)]}
    assert stalled(progress)
    progress["completed"][-1]["run"] = 1
    assert not stalled(progress)
    progress["completed"][-1].update(run=0, violations=60)
    assert not stalled(progress)
    progress["completed"][-1]["violations"] = 0
    assert not stalled(progress)


def test_overnight_plateau_stops_late_flat_counts_but_allows_progress():
    entries = [{"run": 0, "iteration": i, "violations": 210} for i in range(47, 52)]
    assert plateau_stop_reason({"completed": entries})
    entries[-1]["violations"] = 209
    assert plateau_stop_reason({"completed": entries}) is None
    entries[-1].update(violations=210, run=1)
    assert plateau_stop_reason({"completed": entries}) is None
    for entry in entries:
        entry.update(run=0, violations=0)
    assert plateau_stop_reason({"completed": entries}) is None


def test_search_script_rejects_changed_tool_source():
    with pytest.raises(ValueError, match="pinned"):
        search_script("lappend drt_args -or_seed 42", 43, 1)


def test_search_changes_seed_and_enables_actual_order_swaps(monkeypatch):
    import hashlib
    from tools import v2_route_flow
    original = "before\nlappend drt_args -or_seed 42\nafter\n"
    monkeypatch.setattr(v2_route_flow, "PINNED_DRT_SHA256", hashlib.sha256(original.encode()).hexdigest())
    assert search_script(original, 43, 1) == "before\nlappend drt_args -or_seed 43\nlappend drt_args -or_k 1\nafter\n"
    with pytest.raises(ValueError, match="parameters"):
        search_script(original, -1, 1)


DRC = """violation type: Short
 srcs: net:VPWR net:signal
 bbox = (100.0000, 200.0000) - (101.0000, 201.0000) on Layer Metal2
violation type: Metal Spacing
 srcs: net:a net:b
 bbox = (100.0000, 200.0000) - (101.0000, 201.0000) on Layer Metal3
"""


def test_drc_spatial_and_power_conflict_summary():
    result = drc_summary(DRC)
    assert result["count"] == 2
    assert result["by_layer"] == {"Metal2": 1, "Metal3": 1}
    assert result["by_type"] == {"Short": 1, "Metal Spacing": 1}
    assert result["regions_100um"] == {"100,200": 2}
    assert result["signal_to_power_count"] == 1
    assert drc_summary("")["count"] == 0


@pytest.mark.parametrize("report", ["violation type: Short", DRC.replace("100.0000", "nan"),
                                  DRC.replace("srcs:", "invalid:")])
def test_drc_summary_rejects_truncated_or_invalid_reports(report):
    with pytest.raises(ValueError):
        drc_summary(report)


def test_resource_limits_and_missing_routed_evidence():
    sample = {"disk_free_bytes": trial.MIN_DISK + 1, "memory.current": "100", "memory.events": "oom 0\noom_kill 0"}
    assert trial.resource_reason(sample, 1000) is None
    assert trial.resource_reason(dict(sample, disk_free_bytes=0), 1000)
    assert trial.resource_reason(dict(sample, **{"memory.current": "1001"}), 1000)
    assert trial.resource_reason(dict(sample, **{"memory.events": "oom 1"}), 1000)
    assert trial.resource_reason(dict(sample, **{"memory.swap.current": str(1024**3)}), 1000)
    assert trial.extracted_issues({})


def test_extracted_gate_requires_all_corners_and_route_checks():
    metrics = {"route__drc_errors": 0, "route__antenna_violation__count": 0}
    for corner in closure.CORNERS:
        metrics.update(corner_metrics(corner))
        metrics[f"timing__unannotated_net_filtered__count__corner:{corner}"] = 0
    assert trial.extracted_issues(metrics) == []
    for key in list(metrics):
        if key.startswith("closure__"):
            continue
        incomplete = dict(metrics)
        del incomplete[key]
        assert trial.extracted_issues(incomplete)


def test_saved_guide_audit_checks_routing_limits_not_pin_access_guides():
    rect = SimpleNamespace(xMin=lambda: 0, yMin=lambda: 0, xMax=lambda: 1289280, yMax=lambda: 710640)
    guide = SimpleNamespace(getLayer=lambda: SimpleNamespace(getName=lambda: "Metal1"))
    block = SimpleNamespace(getDbUnitsPerMicron=lambda: 1000, getDieArea=lambda: rect,
        getMinRoutingLayer=lambda: 2, getMaxRoutingLayer=lambda: 4,
        getMinLayerForClock=lambda: 2, getMaxLayerForClock=lambda: 4,
        getNets=lambda: [SimpleNamespace(getGuides=lambda: [guide])], getInsts=lambda: [1])
    assert inspect_block(block)["passed"]
    block.getMinRoutingLayer = lambda: 1
    assert not inspect_block(block)["passed"]
