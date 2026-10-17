"""Check consistency between released metadata, default inputs and regressions."""

import json
from pathlib import Path
import subprocess
import tomllib

import protocol_emulator
from tools import v2_candidate

ROOT = Path(__file__).resolve().parents[2]


def test_default_physical_inputs_match_isolated_v2_preparation(tmp_path):
    prepared = v2_candidate.prepare(tmp_path / "candidate")
    assert (ROOT / "info.yaml").read_bytes() == (prepared / "info.yaml").read_bytes()
    assert 'top_module: "tt_um_mcranny_protocol_emulator_v2"' in (ROOT / "info.yaml").read_text()
    config = json.loads((ROOT / "src/config.json").read_text())
    assert config == json.loads((prepared / "src/config.json").read_text())
    assert config["CLOCK_PERIOD"] == 40
    assert config["MAX_TRANSITION_CONSTRAINT"] == 1.5
    assert config["RT_MIN_LAYER"] == "Metal2"
    assert config["RT_CLOCK_MAX_LAYER"] == "Metal4"
    assert 'tiles: "6x4"' in (ROOT / "info.yaml").read_text()
    assert 'top_module: "tt_um_mcranny_protocol_emulator"' in (ROOT / "physical/v1/info.yaml").read_text()


def test_package_version_and_cli_selection_are_consistent():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert metadata["version"] == protocol_emulator.__version__ == "2.0.0rc1"
    assert metadata["scripts"] == {"protocol-emulator": "protocol_emulator.cli:main"}


def test_default_and_explicit_v1_make_targets_select_their_own_tops():
    default = subprocess.check_output(["make", "-n", "lint", "synth", "formal"], cwd=ROOT, text=True)
    legacy = subprocess.check_output(["make", "-n", "v1-lint", "v1-synth", "v1-formal"], cwd=ROOT, text=True)
    assert "--top-module tt_um_mcranny_protocol_emulator_v2 " in default
    assert "synth -top tt_um_mcranny_protocol_emulator_v2;" in default
    for name in ["engine", "capture", "input_filter", "integration"]:
        assert f"formal/v2_{name}.ys" in default
    assert "--top-module tt_um_mcranny_protocol_emulator " in legacy
    assert "synth -top tt_um_mcranny_protocol_emulator;" in legacy
    assert "formal/engine.ys" in legacy
    assert "v2_" not in legacy
