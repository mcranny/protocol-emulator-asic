"""The closure timing gate rejects stale, missing and unsafe evidence."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import v2_closure as closure
from tools.v2_closure_annotation import audit, unannotated_drivers


def passing_metrics(corner):
    metrics = {"closure__global_route_parasitics": 1, "closure__propagated_clock": 1}
    for rule in ("slew", "cap", "fanout"):
        metrics[f"design__max_{rule}_violation__count__corner:{corner}"] = 0
    for kind in ("setup", "hold"):
        for suffix in ("", "_r2r"):
            metrics[f"timing__{kind}{suffix}_vio__count__corner:{corner}"] = 0
            metrics[f"timing__{kind}{suffix}__ws__corner:{corner}"] = 0.01
    return metrics


@pytest.mark.parametrize("corner", closure.CORNERS)
def test_gate_requires_fresh_corner_metrics(corner):
    assert closure.timing_issues(passing_metrics(corner), corner) == []
    other = next(value for value in closure.CORNERS if value != corner)
    assert closure.timing_issues(passing_metrics(other), corner)


@pytest.mark.parametrize("value", [None, True, "0", float("nan"), float("inf"), -0.001, 1e30])
def test_gate_rejects_invalid_or_failing_slack(value):
    corner = closure.CORNERS[0]
    metrics = passing_metrics(corner)
    metrics[f"timing__hold__ws__corner:{corner}"] = value
    assert closure.timing_issues(metrics, corner)


@pytest.mark.parametrize("key,value", [
    ("design__max_slew_violation__count", 1),
    ("design__max_cap_violation__count", -1),
    ("design__max_fanout_violation__count", False),
    ("timing__setup_r2r_vio__count", 1),
])
def test_gate_rejects_electrical_and_timing_violations(key, value):
    corner = closure.CORNERS[0]
    metrics = passing_metrics(corner)
    metrics[f"{key}__corner:{corner}"] = value
    assert closure.timing_issues(metrics, corner)


def test_gate_requires_propagation_and_global_route_audits():
    corner = closure.CORNERS[0]
    for key in ("closure__global_route_parasitics", "closure__propagated_clock"):
        metrics = passing_metrics(corner)
        del metrics[key]
        assert closure.timing_issues(metrics, corner)


def test_mount_validation_uses_nested_mount_and_rejects_escapes(tmp_path):
    root, tt = tmp_path / "work", tmp_path / "tt"
    root.mkdir()
    tt.mkdir()
    (tt / "input").write_text("data")
    mounts = {"/work": root, "/work/tt": tt}
    assert closure.mapped_path("/work/tt/input", mounts) == tt / "input"
    for path in ("/work/../input", "/elsewhere/input", "/work/missing"):
        with pytest.raises(ValueError):
            closure.mapped_path(path, mounts)
    (root / "escape").symlink_to(tt / "input")
    with pytest.raises(ValueError, match="escapes"):
        closure.mapped_path("/work/escape", mounts)


@pytest.fixture
def provenance(tmp_path):
    source, sweep, previous = [tmp_path / name for name in ("source", "sweep", "previous")]
    for directory in (source / "src", previous / "src", sweep):
        directory.mkdir(parents=True)
    hashes = {}
    for name in closure.RTL:
        for directory in (source, previous):
            (directory / "src" / name).write_text(name)
        hashes[name] = closure.sha(source / "src" / name)
    config = dict(closure.FIXED, STA_CORNERS=list(closure.CORNERS))
    closure.write(sweep / "margin005.json", config)
    step = sweep / closure.CHECKPOINT
    step.parent.mkdir(parents=True)
    repair = step.parent.parent / "09-openroad-resizertimingpostgrt"
    repair.mkdir()
    (repair / "design.odb").write_text("ODB fixture")
    state = {"odb": "/trial/runs/margin005/09-openroad-resizertimingpostgrt/design.odb",
             "metrics": {"stale": 1}}
    closure.write(step, state)
    closure.write(repair / "state_out.json", state)
    closure.write(step.parent.parent / "resolved.json", config)
    closure.write(previous / "initial.json", {})
    closure.write(previous / "local-evidence.json", {
        "image": closure.IMAGE, "pdk_commit": closure.PDK_REV,
        "support_tools_commit": closure.TT_REV,
        "files": {f"src/{name}": digest for name, digest in hashes.items()}})
    closure.write(sweep / "manifest.json", {"rtl_sha256": hashes,
        "initial_state": "/work/initial.json",
        "input_sha256": {"margin005.json": closure.sha(sweep / "margin005.json")}})
    closure.write(sweep / "sweep-status.json", [{"label": "margin005", "exit_code": 0}])
    return source, sweep, previous, {"/trial": sweep, "/work": previous}


def test_provenance_accepts_matched_checkpoint(provenance):
    config, state, hashes = closure.verify_inputs(*provenance)
    assert config["RT_MIN_LAYER"] == "Metal2"
    assert state["odb"] in hashes


def test_prepare_clears_metrics_and_keeps_predecessors_read_only(provenance, tmp_path, monkeypatch):
    _, sweep, previous, _ = provenance
    from tools import validate_local
    monkeypatch.setattr(closure, "verify_checkout", lambda *args: None)
    verify = closure.verify_inputs
    monkeypatch.setattr(closure, "verify_inputs", lambda *args: verify(*provenance))
    monkeypatch.setattr(validate_local, "source_state", lambda _: {"source_dirty": True})
    def output(*argv):
        if argv[:3] == ("docker", "image", "inspect"):
            return json.dumps([{"Id": "image-id"}])
        if argv[:2] == ("docker", "info"):
            return json.dumps({"MemTotal": 1024})
        if argv[:2] == ("docker", "ps"):
            return ""
        raise AssertionError(f"unexpected launch: {argv}")
    monkeypatch.setattr(closure, "output", output)
    args = SimpleNamespace(sweep=sweep, predecessor=previous, pdk_root=tmp_path,
                           tt_root=tmp_path, output=tmp_path / "new", name="timing", launch=False)
    destination = closure.prepare(args)
    assert closure.read(destination / "state.json")["metrics"] == {}
    config = closure.read(destination / "config.json")
    assert config["DRT_SAVE_SNAPSHOTS"] is True
    assert config["RT_MIN_LAYER"] == "Metal2"
    assert closure.read(sweep / closure.CHECKPOINT)["metrics"] == {"stale": 1}
    command = closure.read(destination / "manifest.json")["command"]
    volumes = [command[index + 1] for index, value in enumerate(command) if value == "-v"]
    assert len(volumes) == 5
    assert all(value.endswith(":ro") for value in volumes[1:])
    assert "--detach" in command and "--rm" not in command
    with pytest.raises(FileExistsError):
        closure.prepare(args)


def test_step_discovery_accepts_actual_flow_numbering_and_rejects_ambiguity(tmp_path):
    with pytest.raises(ValueError):
        closure.timing_step(tmp_path)
    step = tmp_path / "1-openroad-stamidpnr"
    step.mkdir()
    assert closure.timing_step(tmp_path) == step
    (tmp_path / "01-openroad-stamidpnr").mkdir()
    with pytest.raises(ValueError):
        closure.timing_step(tmp_path)


@pytest.mark.parametrize("change", ["rtl", "config", "missing", "failed", "views", "dependency"])
def test_provenance_rejects_incompatible_evidence(provenance, change):
    source, sweep, previous, _ = provenance
    if change == "rtl":
        (source / "src/v2_capture.v").write_text("changed capture")
    elif change == "config":
        config = closure.read(sweep / "margin005.json")
        config["RT_MIN_LAYER"] = "Metal1"
        closure.write(sweep / "margin005.json", config)
    elif change == "missing":
        (sweep / "runs/margin005/09-openroad-resizertimingpostgrt/design.odb").unlink()
    elif change == "failed":
        closure.write(sweep / "sweep-status.json", [{"label": "margin005", "exit_code": 2}])
    elif change == "views":
        state = closure.read(sweep / closure.CHECKPOINT)
        state["odb"] = "/work/initial.json"
        closure.write(sweep / closure.CHECKPOINT, state)
    else:
        data = closure.read(previous / "local-evidence.json")
        data["pdk_commit"] = "unrelated"
        closure.write(previous / "local-evidence.json", data)
    with pytest.raises(ValueError):
        closure.verify_inputs(*provenance)


def report(names, partial=0):
    return ("report_parasitic_annotation -report_unannotated\n====\n"
            f"Found {len(names)} unannotated drivers.\n"
            + "".join(f" {name}\n" for name in names)
            + f"Found {partial} partially unannotated drivers.\n")


def test_annotation_does_not_ignore_connected_nets_without_detailed_wires():
    nets = {"unused": None, "load/X": SimpleNamespace(getITerms=lambda: [1], getBTerms=lambda: []),
            "logic/X": SimpleNamespace(getITerms=lambda: [1, 2], getBTerms=lambda: [])}
    def find(name):
        return SimpleNamespace(getNet=lambda: nets[name]) if name in nets else None
    block = SimpleNamespace(findITerm=find, findBTerm=find)
    result = audit(block, report(["unused", "load/X"]))
    assert result["passed"]
    assert len(result["unconnected_drivers"]) == 2
    for names, partial in ((["logic/X"], 0), (["unknown/X"], 0), ([], 1)):
        assert not audit(block, report(names, partial))["passed"]


def test_annotation_rejects_missing_or_truncated_reports():
    with pytest.raises((ValueError, IndexError)):
        unannotated_drivers("no report")
    with pytest.raises(ValueError):
        unannotated_drivers(report(["a"]).replace("Found 1 unannotated", "Found 2 unannotated"))
