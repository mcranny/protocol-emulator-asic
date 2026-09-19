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
