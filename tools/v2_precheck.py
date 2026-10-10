"""Run TT precheck in a verified, cached runtime without Nix evaluation/network.

The local image is addressed by immutable ID. Export it and the recorded Python
package tree when moving machines. Runtime-only success is not layout acceptance.
"""
import argparse
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

try:
    from .v2_closure import read, sha, write
    from .local_flow import IMAGE, PDK_REV, TT_REV, output, verify_checkout
except ImportError:
    from v2_closure import read, sha, write
    from local_flow import IMAGE, PDK_REV, TT_REV, output, verify_checkout

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_IMAGE = "sha256:d46df0bbfbd1c59312ade75da1c24a28556c29fe773b0839ccf872d4ef46d558"
PYTHON = "/nix/store/82404paf3vf9nmj8gdfwjnz26g32cfq9-python3-3.11.14/bin/python3"
KLAYOUT = "/nix/store/jr3kcr6pjng4d42nr8vbg8bm1pld3lv6-klayout-0.30.4-1/bin/klayout"
MAGIC = "/nix/store/6l3292h1mpwmjcqrjmlmrk6mby92hy28-magic-vlsi-8.3.568/bin/magic"


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(text):
    pins = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if match is None or normalized(match[1]) in pins:
            raise ValueError(f"expected unique exact package pin: {line}")
        pins[normalized(match[1])] = match[2]
    if not pins:
        raise ValueError("empty package lock")
    return pins


def tree_hashes(root):
    return {str(path.relative_to(root)): sha(path) for path in sorted(root.rglob("*"))
            if path.is_file()}


def runtime_probe(root):
    expected = requirements((root / "tt/precheck/requirements.txt").read_text())
    actual = {normalized(distribution.metadata["Name"]): distribution.version
              for distribution in importlib.metadata.distributions(path=["/site"])}
    if actual != expected:
        raise ValueError(f"Python package inventory differs from TT lock: expected {expected}, got {actual}")
    if sys.version_info[:3] != (3, 11, 14):
        raise ValueError(f"unexpected Python runtime: {sys.version}")
    modules = {}
    for name in ("gdstk", "klayout.db", "klayout.rdb", "numpy", "yaml", "pytest"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to("/site"):
            raise ValueError(f"module resolved outside pinned package tree: {name}: {path}")
        modules[name] = str(path)
    versions = {"python": sys.version, "python_packages": actual, "module_paths": modules}
    for name, binary, argument, expected_version in (
        ("klayout", KLAYOUT, "-v", "KLayout 0.30.4"),
        ("magic", MAGIC, "--version", "8.3.568"),
    ):
        actual_version = subprocess.check_output([binary, argument], text=True, stderr=subprocess.STDOUT).strip()
        if actual_version != expected_version:
            raise ValueError(f"unexpected {name} version: {actual_version}")
        versions[name] = actual_version
    write(root / "versions.json", versions)
    # Exercises TT imports and argument parsing without claiming layout results.
    with (root / "runtime-help.log").open("w") as log:
        subprocess.run([PYTHON, "precheck.py", "--help"], cwd=root / "tt/precheck",
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    return versions


def inside():
    root = Path("/out")
    manifest = read(root / "manifest.json")
    stage = {"release_acceptance": False, "runtime_passed": False,
             "precheck_passed": False, "runtime_only": manifest["runtime_only"], "exit_code": 1}
    try:
        for name, expected in manifest["prepared_sha256"].items():
            if sha(root / name) != expected:
                raise ValueError(f"prepared input changed: {name}")
        if tree_hashes(Path("/site")) != manifest["site_sha256"]:
            raise ValueError("Python package tree changed")
        runtime_probe(root)
        stage["runtime_passed"] = True
        if not manifest["runtime_only"]:
            command = [PYTHON, "precheck.py", "--gds", "/out/layout/" + manifest["gds_name"],
                       "--tech", "ihp-sg13cmos5l"]
            write(root / "precheck-command.json", command)
            result = subprocess.run(command, cwd=root / "tt/precheck")
            stage["tool_exit_code"] = result.returncode
            if result.returncode:
                raise ValueError(f"TT precheck failed: exit {result.returncode}")
            try:
                from .check_v2 import passing_xml, PRECHECK_SUITES
            except ImportError:
                from check_v2 import passing_xml, PRECHECK_SUITES
            passing_xml(root / "tt/precheck/reports/results.xml", required_names=PRECHECK_SUITES, minimum=9)
            stage["precheck_passed"] = True
        stage["exit_code"] = 0
    except Exception as error:
        stage["error"] = str(error)
        print(str(error), file=sys.stderr)
    finally:
        write(root / "stage.json", stage)
    return stage["exit_code"]


def prepare(args):
    try:
        from .validate_local import source_state
    except ImportError:
        from validate_local import source_state
    pdk, tt, site, destination = (path.resolve() for path in (args.pdk_root, args.tt_root, args.site, args.output))
    verify_checkout(pdk, PDK_REV)
    verify_checkout(tt, TT_REV)
    requirements((tt / "precheck/requirements.txt").read_text())
    image = json.loads(output("docker", "image", "inspect", RUNTIME_IMAGE))[0]
    base = json.loads(output("docker", "image", "inspect", IMAGE))[0]
    layers = base["RootFS"]["Layers"]
    if image["RootFS"]["Layers"][:len(layers)] != layers:
        raise ValueError("runtime image does not derive from pinned LibreLane image")
    if args.name in output("docker", "ps", "-a", "--format", "{{.Names}}").splitlines():
        raise ValueError(f"container exists: {args.name}")
    site_hashes = tree_hashes(site)
    if not site_hashes:
        raise ValueError("missing Python package tree")
    inputs = []
    if not args.runtime_only:
        for name in ("gds", "lef", "info"):
            path = getattr(args, name)
            if path is None or not path.is_file():
                raise ValueError(f"full precheck requires --{name}")
        if args.gds.suffix != ".gds":
            raise ValueError("expected a .gds layout")
        inputs = [(args.gds, Path("layout") / args.gds.name),
                  (args.lef, Path("layout") / (args.gds.stem + ".lef")),
                  (args.info, Path("layout/info.yaml"))]
    destination.mkdir(parents=True, exist_ok=False)
    # Copy tracked dependencies only; do not import old reports or local edits.
    for name in output("git", "ls-files", cwd=tt).splitlines():
        source = tt / name
        if not source.is_file():
            continue
        target = destination / "tt" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for source, relative in inputs:
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    for name in ("v2_precheck.py", "v2_closure.py", "local_flow.py", "check_v2.py", "check_physical.py"):
        shutil.copy2(ROOT / "tools" / name, destination / name)
    runtime_paths = [entry.removeprefix("PATH=") for entry in image["Config"]["Env"] if entry.startswith("PATH=")]
    if len(runtime_paths) != 1:
        raise ValueError("runtime image must define one PATH for wrapper utilities")
    binary_path = ":".join(str(Path(binary).parent) for binary in (PYTHON, KLAYOUT, MAGIC)) + ":" + runtime_paths[0]
    command = ["docker", "run", "--name", args.name, "--network", "none",
               "-v", f"{destination}:/out", "-v", f"{pdk}:/pdk:ro", "-v", f"{site}:/site:ro",
               "-e", "PYTHONPATH=/site", "-e", "PYTHONDONTWRITEBYTECODE=1", "-e", f"PATH={binary_path}",
               "-e", "PDK_ROOT=/pdk", "-e", "PDK=ihp-sg13cmos5l", "-e", "KLAYOUT_SETUP_MULTICORE=2",
               "-w", "/out", RUNTIME_IMAGE, PYTHON, "/out/v2_precheck.py", "--inside"]
    manifest = {"release_acceptance": False, **source_state(ROOT), "runtime_only": args.runtime_only,
                "image": RUNTIME_IMAGE, "base_image": IMAGE, "pdk_commit": PDK_REV,
                "support_tools_commit": TT_REV, "command": command, "site_directory": str(site),
                "site_sha256": site_hashes, "prepared_sha256": tree_hashes(destination)}
    if args.gds:
        manifest.update(gds_name=args.gds.name, gds_sha256=sha(args.gds))
    write(destination / "manifest.json", manifest)
    with (destination / "precheck.log").open("w") as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
    manifest["container_exit_code"] = result.returncode
    write(destination / "manifest.json", manifest)
    if result.returncode:
        raise ValueError(f"precheck runtime/check failed; see {destination / 'precheck.log'}")
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("pdk-root", "tt-root", "site", "output", "gds", "lef", "info"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--runtime-only", action="store_true")
    parser.add_argument("--name", default="v2-precheck-offline")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        return inside()
    if any(getattr(args, name) is None for name in ("pdk_root", "tt_root", "site", "output")):
        parser.error("--pdk-root, --tt-root, --site and --output are required")
    if args.runtime_only and any(getattr(args, name) for name in ("gds", "lef", "info")):
        parser.error("--runtime-only does not accept layout inputs")
    print(prepare(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
