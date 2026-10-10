"""Live viewer keeps running state, partial reports and stale evidence distinct."""
import json

from tools.v2_route_viewer import snapshot


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))


def test_snapshot_reads_flushed_progress_and_detects_stale_resources(tmp_path):
    write(tmp_path / "manifest.json", {"container_name": "trial", "memory_stop_bytes": 10000})
    write(tmp_path / "route-status.json", {"phase": "running", "started_unix": 1, "passed": False})
    (tmp_path / "flow.log").write_text("Start 0th optimization iteration.\nNumber of violations = 42.\nStart 1st optimization iteration.\nCompleting 20% with 39 violations.\n")
    observer = tmp_path / "observer-new"
    write(observer / "observer-status.json", {"phase": "observing"})
    (observer / "resources.jsonl").write_text(json.dumps({"unix_time": 100, "memory.current": "20"}) + '\n{"partial":')
    write(observer / "drc-summaries/one.json", {"count": 10, "signal_to_power": [{"sources": ["private-net"]}]})
    data = snapshot(tmp_path, {"Running": True, "Status": "running"}, now=200)
    assert data["progress"]["completed"][0]["violations"] == 42
    assert data["progress"]["current"]["progress_percent"] == 20
    assert data["resource_stale"] is True
    assert data["resource_age_seconds"] == 100
    assert data["docker"]["Running"] is True
    assert "signal_to_power" not in data["drc"]
    assert data["drc"]["count"] == 10
    assert data["release_acceptance"] is False


def test_missing_evidence_is_not_reported_as_zero_or_running(tmp_path):
    data = snapshot(tmp_path, {"Status": "unknown"}, now=200)
    assert data["resource"] == {}
    assert data["resource_stale"]
    assert data["resource_age_seconds"] is None
    assert data["drc"] is None
    assert data["progress"]["completed"] == []
    assert "Running" not in data["docker"]


def test_full_resource_history_combines_original_and_corrected_observer(tmp_path):
    observer = tmp_path / "observer-new"
    write(observer / "observer-status.json", {})
    (tmp_path / "resources.jsonl").write_text('{"unix_time":1,"memory.current":"10"}\n')
    (observer / "resources.jsonl").write_text('{"unix_time":2,"memory.current":"20"}\n')
    data = snapshot(tmp_path, {}, now=3)
    assert data["memory_history"] == [{"time": 1, "bytes": 10}, {"time": 2, "bytes": 20}]
    assert data["resource_stale"] is False
    assert data["observer_stale"] is True  # Fresh original resources do not hide a stopped observer.
