"""Decide whether one V3 build meets the release bar, and hash its layout.

The inputs are a full ``tools/local_flow.py`` run, the Tiny Tapeout precheck
JUnit report, and the gate-level cocotb JUnit report. Every check must pass;
the verdict lists each one that did not.
"""
import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

TOP = "tt_um_mcranny_protocol_emulator_v3"
FINAL = Path("physical/v3/runs/local/final")
LAYOUT = {
    "gds": FINAL / f"gds/{TOP}.gds",
    "lef": FINAL / f"lef/{TOP}.lef",
    "netlist": FINAL / f"nl/{TOP}.nl.v",
    "metrics": FINAL / "metrics.json",
}
#: Minimum slack in ns, beyond the zero-violation checks the run already applies.
MARGINS = {
    "timing__setup_r2r__ws__corner:nom_slow_1p08V_125C": 2.0,
    "timing__hold__ws__corner:nom_fast_1p32V_m40C": 0.10,
}
#: Gate-level tests allowed to skip, because they read a net no pin shows.
GATE_SKIPS = {"test_v3_an_unfiltered_pin_sees_a_glitch_the_filter_rejects"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def junit(path: Path) -> dict[str, str]:
    """Each test case's outcome: ``pass``, ``fail`` or ``skip``."""
    outcomes = {}
    for case in ET.parse(path).getroot().iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            outcome = "fail"
        elif case.find("skipped") is not None:
            outcome = "skip"
        else:
            outcome = "pass"
        outcomes[case.get("name")] = outcome
    return outcomes


def run_issues(run: Path) -> list[str]:
    evidence = json.loads((run / "local-evidence.json").read_text())
    issues = []
    if evidence.get("stage") != "full":
        issues.append(f"flow stage is {evidence.get('stage')}, not full")
    if evidence.get("exit_code") != 0:
        issues.append(f"flow exit code {evidence.get('exit_code')}")
    issues += [f"flow: {issue}" for issue in evidence.get("route_issues", ["not checked"])]
    if evidence.get("source_dirty") is not False:
        issues.append("flow built from a modified checkout")
    metrics = json.loads((run / LAYOUT["metrics"]).read_text())
    for key, minimum in MARGINS.items():
        value = metrics.get(key)
        if not isinstance(value, (int, float)) or value < minimum:
            issues.append(f"{key} = {value}, below {minimum}")
    return issues


def precheck_issues(outcomes: dict[str, str]) -> list[str]:
    if not outcomes:
        return ["precheck report lists no checks"]
    return [f"precheck {name}: {outcome}" for name, outcome in outcomes.items()
            if outcome != "pass"]


def gate_issues(outcomes: dict[str, str]) -> list[str]:
    issues = [f"gate-level {name}: {outcome}" for name, outcome in outcomes.items()
              if outcome == "fail" or (outcome == "skip" and name not in GATE_SKIPS)]
    if "pass" not in outcomes.values():
        issues.append("gate-level report has no passing test")
    return issues


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--precheck", type=Path, required=True)
    parser.add_argument("--gatelevel", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    evidence = json.loads((args.run / "local-evidence.json").read_text())
    precheck, gate = junit(args.precheck), junit(args.gatelevel)
    issues = run_issues(args.run) + precheck_issues(precheck) + gate_issues(gate)
    metrics = json.loads((args.run / LAYOUT["metrics"]).read_text())
    verdict = {
        "accepted": not issues,
        "issues": issues,
        "source_commit": evidence.get("source_commit"),
        "pdk_commit": evidence.get("pdk_commit"),
        "support_tools_commit": evidence.get("support_tools_commit"),
        "image": evidence.get("image"),
        "margins": {key: metrics.get(key) for key in MARGINS},
        "precheck": precheck,
        "gatelevel": gate,
        "sha256": {name: sha256(args.run / path) for name, path in LAYOUT.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(verdict, indent=2) + "\n")
    print(json.dumps({k: verdict[k] for k in ("accepted", "issues", "margins")}, indent=2))
    return 0 if verdict["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
