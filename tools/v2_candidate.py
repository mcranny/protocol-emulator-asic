"""Prepare an isolated V2 physical-flow input without replacing the V1 release.

The destination must not exist. Pass it to tools/local_flow.py --source.
This records development inputs; it does not establish release acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil

try:
    from .validate_local import source_state
except ImportError:
    from validate_local import source_state


ROOT = Path(__file__).resolve().parents[1]


def prepare(destination):
    config = json.loads((ROOT / "src/config.json").read_text())
    overrides = json.loads((ROOT / "physical/v2/overrides.json").read_text())
    if config["CLOCK_PERIOD"] != 40 or config["MAX_TRANSITION_CONSTRAINT"] != 1.5:
        raise ValueError("V2 requires the 40 ns clock and 1.5 ns transition constraint")
    if set(overrides) - {"CTS_DISTANCE_BETWEEN_BUFFERS", "GRT_DESIGN_REPAIR_MAX_WIRE_LENGTH",
                         "PL_MAX_DISPLACEMENT_X", "PL_MAX_DISPLACEMENT_Y", "CTS_APPLY_NDR",
                         "GRT_ADJUSTMENT", "GRT_DESIGN_REPAIR_MAX_SLEW_PCT",
                         "GRT_ANTENNA_REPAIR_MARGIN", "GRT_ANTENNA_REPAIR_ITERS",
                         "DRT_ANTENNA_REPAIR_ITERS", "DRT_ANTENNA_REPAIR_JUMPER_ONLY",
                         "RT_MIN_LAYER", "RT_CLOCK_MIN_LAYER", "RT_CLOCK_MAX_LAYER",
                         "PL_RESIZER_HOLD_SLACK_MARGIN", "GRT_RESIZER_HOLD_SLACK_MARGIN",
                         "DRT_ANTENNA_REPAIR_DIODE_ONLY"}:
        raise ValueError("unexpected V2 physical override; review required")
    fixed = {"RT_MIN_LAYER": "Metal2", "RT_CLOCK_MIN_LAYER": "Metal2",
             "RT_CLOCK_MAX_LAYER": "Metal4", "PL_RESIZER_HOLD_SLACK_MARGIN": 0.05,
             "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.05,
             "DRT_ANTENNA_REPAIR_JUMPER_ONLY": False, "DRT_ANTENNA_REPAIR_DIODE_ONLY": True}
    for key, expected in fixed.items():
        if overrides.get(key) != expected:
            raise ValueError(f"V2 closure override changed: {key}")
    config.update(overrides)
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "src").mkdir()
    shutil.copy2(ROOT / "physical/v2/info.yaml", destination / "info.yaml")
    for filename in ("host_spi.v", "v2_engine.v", "v2_capture.v", "v2_input_filter.v", "v2_project.v"):
        shutil.copy2(ROOT / "src" / filename, destination / "src" / filename)
    (destination / "src/config.json").write_text(json.dumps(config, indent=2) + "\n")
    manifest = {
        "schema": 1, "release_acceptance": False,
        **source_state(ROOT),
        "prepared": {str(p.relative_to(destination)): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(destination.rglob("*")) if p.is_file()},
    }
    (destination / "candidate-inputs.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(prepare(parser.parse_args().output))
