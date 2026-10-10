"""Record source, tools, seeds, and checksums without collecting host secrets."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "error": type(exc).__name__}
    return {"exit_code": result.returncode,
            "output": (result.stdout + result.stderr).strip()}


def checksums(paths):
    result = {}
    for path in sorted(set(paths)):
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[str(path)] = {"sha256": digest.hexdigest(), "bytes": path.stat().st_size}
    return result


def collect(kind):
    commit = command(["git", "rev-parse", "HEAD"])
    if commit.get("exit_code") != 0:
        raise ValueError("source revision unavailable")
    source = commit["output"]
    if os.environ.get("GITHUB_SHA", source) != source:
        raise ValueError("checkout does not match workflow source")
    status = command(["git", "status", "--porcelain"])
    if status.get("exit_code") != 0:
        raise ValueError("source cleanliness unavailable")
    patterns = ["test/results*.xml", "test/output/*.json", "test/output/*.vcd", "test/*.fst",
                "test/sim_build/core/*.fst", "build/formal-*.log",
                "build/python-results.xml", "build/synthesis.log", "src/*.v",
                "src/config.json", "info.yaml", "test/requirements.txt",
                "formal/*.ys", "examples/*.asm", "test/test*.py", "test/Makefile*",
                "tests/test_v2*.py", "build/v2-mutations/results.json",
                "host/protocol_emulator/*.py", "host/protocol_emulator/v2/*.py",
                "physical/v2/*", "tools/v2_*.py"]
    paths = [path for pattern in patterns for path in Path(".").glob(pattern)]
    if kind == "gatelevel":
        paths += [Path("test/gate_level_netlist.v"), Path("tt_submission/pdk.json"),
                  Path("tt_submission/commit_id.json")]
        root = os.environ.get("PDK_ROOT")
        if root:
            paths += list((Path(root) / "ihp-sg13cmos5l/libs.ref").glob("*/verilog/*.v"))
    packages = {}
    for package in ("cocotb", "pytest"):
        try:
            packages[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            packages[package] = None
    return {"schema": 1, "kind": kind, "source_commit": source,
            "source_dirty": bool(status["output"]),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "workflow_run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
            "python": sys.version, "packages": packages,
            "tools": {name: command(args) for name, args in {
                "iverilog": ["iverilog", "-V"], "verilator": ["verilator", "--version"],
                "yosys": ["yosys", "-V"], "git": ["git", "--version"]}.items()},
            "files": checksums(paths)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=("rtl", "gatelevel"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = collect(args.kind)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
