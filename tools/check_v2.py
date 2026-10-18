"""Fail-closed V2 local acceptance with exact suite identities and input hashes.

An acceptance bundle must contain evidence.json, source/ (the accepted source
snapshot) and artifacts/ (all logs, results and physical outputs). Every file
referenced by the manifest is checked, and every stage must name the same clean
commit and source inventory. Creating a manifest does not establish acceptance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

try:
    from .check_physical import check, passing_xml
except ImportError:  # Direct command-line invocation.
    from check_physical import check, passing_xml

PIN_SUITES = {
    "test_v2_pin_transport_safety", "test_v2_capture_host_and_noninterference",
    "test_v2_uart_fault_capture_and_scenario", "test_v2_bounded_firmware_playback",
    "test_v2_full_duplex_host_budget", "test_v2_uart_megabaud_buffered_duplex",
    "test_v2_spi_target_independent_peer", "test_v2_spi_controller_independent_decoder",
    "test_v2_i2c_controller_independent_peer", "test_v2_i2c_target_independent_peer",
    "test_v2_protocol_faults",
    "test_v2_spi_i2c_fault_scenarios",
    "test_v2_input_filter_spikes_and_bypass",
    "test_v2_custom_pulse_protocol",
}
CORE_SUITES = {"test_v2_uart_differential", "test_v2_instruction_differential", "test_v2_fifo_boundary_differential"}
PRECHECK_SUITES = {"KLayout pin label overlapping drawing", "KLayout SG13CMOS5L DRC",
    "KLayout zero area", "KLayout Checks", "Pin check", "Boundary check", "Layer check",
    "Cell name check", "Analog pin check"}
MUTATIONS = {"rx_drop", "tx_byte_order", "open_drain_high", "wait_edge", "capture_timestamp", "ownership_overlap"}
STAGES = {"functional", "physical", "precheck", "gatelevel", "render"}
ARTIFACTS = {"python.xml", "v1-core.xml", "v1-pins.xml", "v2-core.xml", "v2-pins.xml",
             "capture.xml", "gatelevel.xml", "precheck.xml", "metrics.json", "config.json",
             "formal-engine.log", "formal-v2-engine.log", "formal-v2-capture.log",
             "formal-v2-integration.log", "mutations.json", "netlist.v", "layout.gds",
             "formal-v2-input-filter.log",
             "layout.svg", "render-manifest.json", "python-collection.txt",
             "initialization.json", "reset-rtl.json", "v1-reset.xml", "v2-reset.xml",
             "reset-gatelevel.json", "v2-reset-gatelevel.xml"}


REQUIRED_INPUTS = {"src/engine.v", "src/project.v", "test/reset_tb.v", "test/Makefile.reset",
                   "test/support/reset.py", "test/v1/test_reset.py", "test/v2/test_reset.py",
                   "tools/validate_reset.py", "tools/audit_initialization.py", "src/host_spi.v", "src/v2_engine.v", "src/v2_capture.v", "src/v2_project.v",
                   "src/config.json", "physical/v2/info.yaml", "physical/v2/overrides.json",
                   "host/protocol_emulator/v2/firmware.py", "test/v2/test_pins.py", "test/support/pins.py", "test/support/v2_pins.py",
                   "src/v2_input_filter.v", "formal/v2_input_filter.ys",
                   "host/protocol_emulator/v2/custom_protocol.py", "formal/v2_engine.ys", "formal/v2_capture.ys", "formal/v2_integration.ys"}

def checksum(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def verify_files(root, entries):
    if not isinstance(entries, dict) or not entries:
        raise ValueError("missing file inventory")
    for name, expected in entries.items():
        path = root / name
        if Path(name).is_absolute() or '..' in Path(name).parts or not path.is_file():
            raise ValueError(f"invalid or missing evidence file: {name}")
        if checksum(path) != expected:
            raise ValueError(f"checksum mismatch: {name}")


def python_suites(results, collection):
    """Match every executed pytest identity to a fresh collection from the snapshot."""
    expected = []
    for line in collection.read_text().splitlines():
        if line.startswith("tests/") and "::" in line:
            parts = line.split("::")
            module = parts[0].removesuffix(".py").replace("/", ".")
            expected.append((".".join([module, *parts[1:-1]]), parts[-1]))
    actual = [(case.get("classname"), case.get("name"))
              for case in ET.parse(results).getroot().iter("testcase")]
    if (not expected or len(set(expected)) != len(expected)
            or sorted(actual) != sorted(expected)):
        raise ValueError("Python test identities differ from the collected source suite")
    return passing_xml(results, minimum=len(expected))


def accept(bundle):
    bundle = Path(bundle)
    data = json.loads((bundle / "evidence.json").read_text())
    source = data.get("source_commit", "")
    if data.get("schema") != 2 or not re.fullmatch('[0-9a-f]{40}', source):
        raise ValueError("invalid V2 acceptance manifest")
    inputs, artifacts = data.get("inputs"), data.get("artifacts")
    verify_files(bundle / "source", inputs)
    verify_files(bundle / "artifacts", artifacts)
    if not REQUIRED_INPUTS <= inputs.keys() or not ARTIFACTS <= artifacts.keys():
        raise ValueError("incomplete source or artifact inventory")
    stages = data.get("stages", {})
    if set(stages) != STAGES:
        raise ValueError("missing or unexpected acceptance stages")
    for name, stage in stages.items():
        if (stage.get("source_commit") != source or stage.get("source_dirty") is not False
                or stage.get("exit_code") != 0 or stage.get("inputs") != inputs):
            raise ValueError(f"stale, dirty or failed stage: {name}")
    if stages["gatelevel"].get("netlist_sha256") != artifacts["netlist.v"]:
        raise ValueError("gate-level netlist mismatch")
    if stages["precheck"].get("gds_sha256") != artifacts["layout.gds"]:
        raise ValueError("precheck GDS mismatch")
    root = bundle / "artifacts"
    read = lambda name: json.loads((root / name).read_text())
    render = read("render-manifest.json")
    if (render.get("source_sha256") != artifacts["layout.gds"]
            or render.get("svg_sha256") != artifacts["layout.svg"]
            or render.get("geometry_simplified") is not False or render.get("layers_omitted") != []
            or render.get("svg_inventory_matches_gds") is not True
            or render.get("hierarchy_depth_limit", 0) is not None):
        raise ValueError("render does not preserve the accepted GDS")
    config = read("config.json")
    if config.get("DESIGN_NAME") != "tt_um_mcranny_protocol_emulator_v2":
        raise ValueError("physical evidence is not the V2 top")
    result = check(read("metrics.json"), config, root / "precheck.xml", root / "gatelevel.xml",
                   pin_suites=PIN_SUITES, precheck_suites=PRECHECK_SUITES)
    for filename, suites in (("v2-core.xml", CORE_SUITES), ("v2-pins.xml", PIN_SUITES),
                             ("capture.xml", {"test_v2_capture_differential"}),
                             ("v1-core.xml", {"test_differential_directed", "test_differential_random", "test_differential_uart"}),
                             ("v1-pins.xml", {"test_host_protocol_and_safety", "test_uart_firmware_acceptance", "test_host_phase_and_interrupted_uart"})):
        passing_xml(root / filename, required_names=suites)
    try:
        from .validate_reset import check_initialization, check_record
    except ImportError:
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from tools.validate_reset import check_initialization, check_record
    identity = dict(source_commit=source, source_dirty=False, inputs=inputs)
    check_initialization(read("initialization.json"), inputs)
    check_record(read("reset-rtl.json"), (1, 2), identity, "rtl")
    check_record(read("reset-gatelevel.json"), (2,), identity, "gatelevel", artifacts["netlist.v"])
    for filename, version in (("v1-reset.xml", 1), ("v2-reset.xml", 2), ("v2-reset-gatelevel.xml", 2)):
        passing_xml(root / filename, required_names={f"test_v{version}_reset_and_state_loss"})
    result["python_tests"] = python_suites(root / "python.xml", root / "python-collection.txt")
    for name in ("engine", "v2-engine", "v2-capture", "v2-integration", "v2-input-filter"):
        if "Induction step proven: SUCCESS!" not in (root / f"formal-{name}.log").read_text():
            raise ValueError(f"missing formal induction: {name}")
    mutations = read("mutations.json")
    if (len(mutations) != len(MUTATIONS) or {m["mutation"] for m in mutations} != MUTATIONS
            or any(m.get("detected") is not True for m in mutations)):
        raise ValueError("missing or undetected mutation")
    result.update(source_commit=source, acceptance="V2 local", render="full geometry")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(accept(args.bundle), indent=2))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        parser.exit(1, f"V2 acceptance failed: {exc}\n")
