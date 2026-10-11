"""Require selected RTL defects to fail their independent acceptance tests.

Mutants and reports are written under build/, never into the source tree.
Compilation errors and missing/empty result files do not count as detections.
"""
import argparse
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("rx_drop", "v2_engine.v", "wire rx_push = execute && fetch_ok", "wire rx_push = 1'b0 && execute && fetch_ok",
     "Makefile.v2", "test_v2_uart_differential"),
    ("tx_byte_order", "v2_engine.v", "tx_mem[tx_wr + 4'd2] <= tx_data[23:16]", "tx_mem[tx_wr + 4'd2] <= tx_data[15:8]",
     "Makefile.v2", "test_v2_fifo_boundary_differential"),
    ("open_drain_high", "v2_engine.v", " & ~(out_value & open_drain)", "",
     "Makefile.v2pins", "test_v2_pin_transport_safety"),
    ("wait_edge", "v2_engine.v", "pins_in[wait_pin] == wait_level", "pins_in[wait_pin] != wait_level",
     "Makefile.v2", "test_v2_instruction_differential"),
    ("capture_timestamp", "v2_capture.v", "timestamp + 1'b1", "timestamp + 2'd2",
     "Makefile.v2capture", "test_v2_capture_differential"),
]


def main(output=None):
    output = Path(output or ROOT / "build/v2-mutations").resolve()
    results = []
    for name, filename, before, after, makefile, case in CASES:
        source = (ROOT / "src" / filename).read_text()
        if source.count(before) != 1:
            raise ValueError(f"mutation anchor is not unique: {name}")
        directory = output / name
        directory.mkdir(parents=True, exist_ok=True)
        mutant = directory / filename
        mutant.write_text(source.replace(before, after, 1))
        sources = [mutant]
        if makefile.endswith("pins"):
            sources += [ROOT / "src/host_spi.v", ROOT / "src/v2_capture.v", ROOT / "src/v2_input_filter.v", ROOT / "src/v2_project.v"]
        result_path = directory / "results.xml"
        result_path.unlink(missing_ok=True)
        command = ["make", "-C", str(ROOT / "test"), "-f", makefile,
                   "VERILOG_SOURCES=" + " ".join(str(path) for path in sources),
                   f"SIM_BUILD={directory / 'sim'}", f"COCOTB_RESULTS_FILE={result_path}",
                   f"COCOTB_TEST_FILTER={case}"]
        with (directory / "test.log").open("w") as log:
            completed = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, env=os.environ)
        if not result_path.exists():
            raise RuntimeError(f"mutation did not execute a test: {name}")
        cases = [entry for entry in ET.parse(result_path).getroot().iter("testcase")
                 if entry.find("skipped") is None]
        detected = (completed.returncode != 0 and len(cases) == 1 and cases[0].get("name") == case
                    and cases[0].find("failure") is not None)
        results.append({"mutation": name, "test": case, "detected": detected,
                        "returncode": completed.returncode})
        divergence = ROOT / "test/output" / ("v2-capture-divergence.json" if makefile == "Makefile.v2capture"
                                             else "v2-divergence.json")
        if divergence.exists() and makefile in ("Makefile.v2", "Makefile.v2capture"):
            shutil.copy2(divergence, directory / "divergence.json")
    destination = output / "results.json"
    # A counterexample must also expose a disabled ownership check in the
    # compositional integration proof; a parser/tool failure is not detection.
    name = "ownership_overlap"
    directory = output / name
    directory.mkdir(parents=True, exist_ok=True)
    source = (ROOT / "src/v2_project.v").read_text()
    anchor = "|(payload[7:0] & (select_engine ? owner0 : owner1))"
    if source.count(anchor) != 1:
        raise ValueError("ownership mutation anchor is not unique")
    mutant = directory / "v2_project.v"
    mutant.write_text(source.replace(anchor, "1'b0"))
    script = directory / "proof.ys"
    script.write_text((ROOT / "formal/v2_integration.ys").read_text().replace("src/v2_project.v", str(mutant)))
    log_path = directory / "proof.log"
    with (directory / "test.log").open('w') as log:
        completed = subprocess.run(['yosys', '-Q', '-T', '-s', str(script), '-l', str(log_path)],
                                   cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    log_text = log_path.read_text() if log_path.exists() else ''
    detected = bool(completed.returncode != 0 and "proof did fail" in log_text
                    and re.search(r"model found.*: FAIL!", log_text))
    results.append({"mutation": name, "test": "v2_integration_induction", "detected": detected,
                    "returncode": completed.returncode})
    destination.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0 if all(result["detected"] for result in results) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    raise SystemExit(main(parser.parse_args().output))
