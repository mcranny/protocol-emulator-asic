"""Prepare an isolated V2 physical-flow input without replacing the V1 release.

The destination must not exist. Pass it to tools/local_flow.py --source.
This records development inputs; it does not establish release acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def prepare(destination):
    config = json.loads((ROOT / "src/config.json").read_text())
    overrides = json.loads((ROOT / "physical/v2/overrides.json").read_text())
    if config["CLOCK_PERIOD"] != 40 or config["MAX_TRANSITION_CONSTRAINT"] != 1.5:
        raise ValueError("V2 requires the 40 ns clock and 1.5 ns transition constraint")
    if set(overrides) - {"CTS_DISTANCE_BETWEEN_BUFFERS", "GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH"}:
        raise ValueError("unexpected V2 physical override; review required")
    config.update(overrides)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "src").mkdir()
    shutil.copy2(ROOT / "physical/v2/info.yaml", destination / "info.yaml")
    for filename in ("host_spi.v", "v2_engine.v", "v2_capture.v", "v2_project.v"):
        shutil.copy2(ROOT / "src" / filename, destination / "src" / filename)
    (destination / "src/config.json").write_text(json.dumps(config, indent=2) + "\n")
    patterns = ("src/*.v", "src/config.json", "physical/v2/*", "host/protocol_emulator/*.py",
                "host/protocol_emulator/v2/*.py", "formal/*.ys", "test/test*.py",
                "test/Makefile*", "test/requirements.txt", "tests/test_v2*.py", "tools/*.py")
    paths = sorted({p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()})
    manifest = {
        "schema": 1, "release_acceptance": False,
        "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_dirty": bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT)),
        "inputs": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "prepared": {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(destination.rglob("*")) if p.is_file()},
    }
    (destination / "candidate-inputs.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(prepare(parser.parse_args().output))
