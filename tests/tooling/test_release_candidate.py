"""Check consistency between released metadata and the package version."""

from pathlib import Path
import tomllib

import protocol_emulator

ROOT = Path(__file__).resolve().parents[2]


def test_package_version_and_cli_selection_are_consistent():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert metadata["version"] == protocol_emulator.__version__ == "3.0.0.dev0"
    assert metadata["scripts"] == {"protocol-emulator": "protocol_emulator.cli:main"}
    assert metadata["description"] == "Toolchain and model for the V3 protocol emulator ASIC"
