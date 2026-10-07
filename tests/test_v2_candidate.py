"""V2 physical candidates preserve constraints with qualified routing settings."""
import hashlib
import json
import shutil

import pytest

from tools import local_flow, v2_candidate


@pytest.fixture
def candidate_source(tmp_path, monkeypatch):
    root = tmp_path / "source"
    for name in ("src/config.json", "physical/v2/info.yaml", "physical/v2/overrides.json",
                 "src/host_spi.v", "src/v2_engine.v", "src/v2_capture.v",
                 "src/v2_input_filter.v", "src/v2_project.v"):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(v2_candidate.ROOT / name, target)
    monkeypatch.setattr(v2_candidate, "ROOT", root)
    monkeypatch.setattr(v2_candidate, "source_state", lambda _: {
        "source_commit": "a" * 40, "source_dirty": False, "inputs": {},
    })
    return root


def test_candidate_resolves_signal_and_clock_ranges_without_relaxing_limits(candidate_source, tmp_path):
    original = (candidate_source / "src/config.json").read_bytes()
    destination = v2_candidate.prepare(tmp_path / "candidate")
    config = json.loads((destination / "src/config.json").read_text())
    assert (destination / "info.yaml").read_bytes() == (candidate_source / "physical/v2/info.yaml").read_bytes()
    project = {"language": "Verilog", "tiles": "6x4", "clock_hz": 25_000_000,
               "top_module": "tt_um_mcranny_protocol_emulator_v2",
               "source_files": ["host_spi.v", "v2_engine.v", "v2_capture.v",
                                "v2_input_filter.v", "v2_project.v"]}
    config.update(local_flow.user_config(project, {"6x4": "0 0 1289.28 710.64"}))
    assert (config["RT_MIN_LAYER"], config["RT_MAX_LAYER"]) == ("Metal2", "Metal4")
    assert (config["RT_CLOCK_MIN_LAYER"], config.get("RT_CLOCK_MAX_LAYER", config["RT_MAX_LAYER"])) == ("Metal2", "Metal4")
    assert config["PL_RESIZER_HOLD_SLACK_MARGIN"] == 0.05
    assert config["DRT_ANTENNA_REPAIR_DIODE_ONLY"] is True
    assert config["DRT_ANTENNA_REPAIR_JUMPER_ONLY"] is False
    assert config["CLOCK_PERIOD"] == 40
    assert config["MAX_TRANSITION_CONSTRAINT"] == 1.5
    assert config["DIE_AREA"] == "0 0 1289.28 710.64"
    assert (candidate_source / "src/config.json").read_bytes() == original
    assert "DRT_SAVE_SNAPSHOTS" not in config
    manifest = json.loads((destination / "candidate-inputs.json").read_text())
    assert manifest["prepared"]["src/config.json"] == hashlib.sha256(
        (destination / "src/config.json").read_bytes()).hexdigest()


@pytest.mark.parametrize("key,value", [("CLOCK_PERIOD", 41), ("MAX_TRANSITION_CONSTRAINT", 2)])
def test_candidate_rejects_relaxed_timing_before_writing(candidate_source, tmp_path, key, value):
    path = candidate_source / "src/config.json"
    config = json.loads(path.read_text())
    config[key] = value
    path.write_text(json.dumps(config))
    destination = tmp_path / "candidate"
    with pytest.raises(ValueError, match="40 ns clock and 1.5 ns"):
        v2_candidate.prepare(destination)
    assert not destination.exists()


@pytest.mark.parametrize("key,value", [
    ("RT_MAX_LAYER", "TopMetal1"), ("CLOCK_PERIOD", 41), ("MAX_TRANSITION_CONSTRAINT", 2),
])
def test_candidate_rejects_unreviewed_overrides(candidate_source, tmp_path, key, value):
    path = candidate_source / "physical/v2/overrides.json"
    overrides = json.loads(path.read_text())
    overrides[key] = value
    path.write_text(json.dumps(overrides))
    destination = tmp_path / "candidate"
    with pytest.raises(ValueError, match="unexpected V2 physical override"):
        v2_candidate.prepare(destination)
    assert not destination.exists()


@pytest.mark.parametrize("key,value", [("RT_MIN_LAYER", "Metal1"),
    ("RT_CLOCK_MAX_LAYER", "TopMetal1"), ("PL_RESIZER_HOLD_SLACK_MARGIN", 0),
    ("DRT_ANTENNA_REPAIR_DIODE_ONLY", False)])
def test_candidate_rejects_drift_from_closure_recipe(candidate_source, tmp_path, key, value):
    path = candidate_source / "physical/v2/overrides.json"
    overrides = json.loads(path.read_text())
    overrides[key] = value
    path.write_text(json.dumps(overrides))
    with pytest.raises(ValueError, match="closure override changed"):
        v2_candidate.prepare(tmp_path / "candidate")
