"""Require selected RTL defects to fail their independent acceptance tests.

Mutants and reports are written under build/, never into the source tree.
Compilation errors and missing/empty result files do not count as detections.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("rx_drop", "wire rx_push = execute && fetch_ok", "wire rx_push = 1'b0 && execute && fetch_ok",
     "Makefile.v2", "v2_uart_differential"),
    ("tx_byte_order", "tx_mem[tx_wr + 4'd2] <= tx_data[23:16]", "tx_mem[tx_wr + 4'd2] <= tx_data[15:8]",
     "Makefile.v2", "v2_fifo_boundary_differential"),
    ("open_drain_high", " & ~(out_value & open_drain)", "",
     "Makefile.v2pins", "v2_pin_transport_safety"),
]


def main():
    source = (ROOT / "src/v2_engine.v").read_text()
    results = []
    for name, before, after, makefile, case in CASES:
        if source.count(before) != 1:
            raise ValueError(f"mutation anchor is not unique: {name}")
        directory = ROOT / "build/v2-mutations" / name
        directory.mkdir(parents=True, exist_ok=True)
        mutant = directory / "v2_engine.v"
        mutant.write_text(source.replace(before, after, 1))
        sources = [mutant]
        if makefile.endswith("pins"):
            sources += [ROOT / "src/host_spi.v", ROOT / "src/v2_project.v"]
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
        divergence = ROOT / "test/output/v2-divergence.json"
        if divergence.exists() and makefile == "Makefile.v2":
            shutil.copy2(divergence, directory / "divergence.json")
    destination = ROOT / "build/v2-mutations/results.json"
    destination.write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))
    return 0 if all(result["detected"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
