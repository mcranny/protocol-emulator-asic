"""Prepare and run fresh all-corner timing for the saved V2 hold005 candidate.

Prepare a provenance-checked candidate and require fresh all-corner timing.
It never launches detailed routing or establishes release acceptance. Predecessor
artifacts are mounted read-only, and every output directory must be new.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import threading
import time

try:
    from .local_flow import IMAGE, PDK_REV, TT_REV, output, verify_checkout
except ImportError:
    from local_flow import IMAGE, PDK_REV, TT_REV, output, verify_checkout

ROOT = Path(__file__).resolve().parents[1]
CORNERS = ("nom_fast_1p32V_m40C", "nom_typ_1p20V_25C", "nom_slow_1p08V_125C")
RTL = ("host_spi.v", "v2_engine.v", "v2_capture.v", "v2_input_filter.v", "v2_project.v")
CHECKPOINT = "runs/margin005/10-openroad-stamidpnr-3/state_out.json"
FIXED = {
    "PL_RESIZER_HOLD_SLACK_MARGIN": 0.05, "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.05,
    "RT_MIN_LAYER": "Metal2", "RT_MAX_LAYER": "Metal4",
    "RT_CLOCK_MIN_LAYER": "Metal2", "RT_CLOCK_MAX_LAYER": "Metal4",
    "CLOCK_PERIOD": 40, "MAX_TRANSITION_CONSTRAINT": 1.5,
    "CLOCK_UNCERTAINTY_CONSTRAINT": 0.25,
    "DIE_AREA": [0, 0, 1289.28, 710.64], "OPENROAD_THREADS": 2, "STA_THREADS": 2,
}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def absolute_paths(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from absolute_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from absolute_paths(child)
    elif isinstance(value, str) and value.startswith("/"):
        yield value


def mapped_path(path, mounts):
    """Resolve longest mount first and reject traversal out of a mounted tree."""
    virtual = PurePosixPath(path)
    if ".." in virtual.parts:
        raise ValueError(f"unsafe artifact path: {path}")
    for target, host in sorted(mounts.items(), key=lambda item: -len(item[0])):
        if virtual.is_relative_to(target):
            result = (host / virtual.relative_to(target)).resolve()
            if not result.is_relative_to(host.resolve()):
                raise ValueError(f"artifact escapes mount: {path}")
            if not result.exists():
                raise ValueError(f"missing artifact: {path}")
            return result
    raise ValueError(f"unmapped artifact: {path}")


def verify_inputs(source, sweep, predecessor, mounts):
    manifest = read(sweep / "manifest.json")
    if set(manifest["rtl_sha256"]) != set(RTL):
        raise ValueError("sweep RTL inventory does not match V2")
    evidence = read(predecessor / "local-evidence.json")
    for key, expected in (("image", IMAGE), ("pdk_commit", PDK_REV),
                          ("support_tools_commit", TT_REV)):
        if evidence[key] != expected:
            raise ValueError(f"predecessor dependency mismatch: {key}")
    for name, expected in manifest["rtl_sha256"].items():
        if (sha(source / "src" / name) != expected
                or sha(predecessor / "src" / name) != expected
                or evidence["files"][f"src/{name}"] != expected):
            raise ValueError(f"RTL changed; rebuild candidate: {name}")
    for name, expected in manifest["input_sha256"].items():
        if Path(name).name != name or sha(sweep / name) != expected:
            raise ValueError(f"sweep input changed: {name}")
    status = read(sweep / "sweep-status.json")
    selected = [entry for entry in status if entry["label"] == "margin005"]
    if len(selected) != 1 or selected[0]["exit_code"] != 0:
        raise ValueError("margin005 sweep did not complete successfully")
    config = read(sweep / "margin005.json")
    if config != read(sweep / "runs/margin005/resolved.json"):
        raise ValueError("sweep configuration differs from executed configuration")
    for key, expected in FIXED.items():
        if config.get(key) != expected:
            raise ValueError(f"fixed constraint mismatch: {key}")
    if set(config["STA_CORNERS"]) != set(CORNERS):
        raise ValueError("unexpected timing corners")
    state = read(sweep / CHECKPOINT)
    # The last STA step must be observational: it must retain the repaired views.
    repaired = read(sweep / "runs/margin005/09-openroad-resizertimingpostgrt/state_out.json")
    if {k: v for k, v in state.items() if k != "metrics"} != {
            k: v for k, v in repaired.items() if k != "metrics"}:
        raise ValueError("timing checkpoint differs from post-GRT repaired views")
    paths = set(absolute_paths({k: v for k, v in state.items() if k != "metrics"}))
    paths.add(manifest["initial_state"])
    artifacts = {p: sha(mapped_path(p, mounts)) for p in sorted(paths)}
    # Config may reference immutable files inside the pinned image. Validate these
    # in the container; all mounted paths can also be checked here.
    for path in absolute_paths(config):
        if not path.startswith("/nix/store/"):
            mapped_path(path, mounts)
    return config, state, artifacts


def timing_issues(metrics, corner):
    zeros = [f"design__max_{rule}_violation__count" for rule in ("slew", "cap", "fanout")]
    slacks = []
    for kind in ("setup", "hold"):
        for suffix in ("", "_r2r"):
            zeros.append(f"timing__{kind}{suffix}_vio__count")
            slacks.append(f"timing__{kind}{suffix}__ws")
    issues = []
    for base in zeros + slacks:
        key = f"{base}__corner:{corner}"
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
            issues.append(f"missing/invalid metric: {key}")
        elif (base in zeros and value != 0) or (base in slacks and not 0 <= value < 1e6):
            issues.append(f"{key} = {value}")
    for key in ("closure__global_route_parasitics", "closure__propagated_clock"):
        if type(metrics.get(key)) not in (int, float) or metrics[key] != 1:
            issues.append(f"missing/invalid audit: {key}")
    return issues


def timing_step(directory):
    steps = list(directory.glob("*-openroad-stamidpnr"))
    if len(steps) != 1 or not steps[0].is_dir():
        raise ValueError("expected exactly one fresh STA step directory")
    return steps[0]


def sample_resources(root):
    sample = {"unix_time": time.time(), "disk_free_bytes": shutil.disk_usage(root).free}
    for name in ("memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak", "memory.events"):
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            sample[name] = path.read_text().strip()
    with (root / "resources.jsonl").open("a") as stream:
        stream.write(json.dumps(sample) + "\n")


def inside():
    """One fresh OpenROAD process and singleton PNR corner per analysis."""
    from librelane.flows import SequentialFlow
    from librelane.steps import Step
    from librelane.state import State

    class CornerTiming(SequentialFlow):
        Steps = [Step.factory.get("OpenROAD.STAMidPNR")]

    root = Path("/closure")
    manifest = read(root / "manifest.json")
    for name, expected in manifest["prepared_sha256"].items():
        if sha(root / name) != expected:
            raise ValueError(f"prepared input changed: {name}")
    for name, expected in manifest["predecessor_artifact_sha256"].items():
        if sha(name) != expected:
            raise ValueError(f"predecessor artifact changed: {name}")
    config = read(root / "config.json")
    state = read(root / "state.json")
    for path in set(absolute_paths(config)) | set(absolute_paths(state)):
        if not Path(path).exists():
            raise ValueError(f"path unavailable under container mounts: {path}")
    versions = {}
    for name, argv in (("openroad", ["openroad", "-version"]),
                       ("librelane", [sys.executable, "-m", "librelane", "--version"])):
        versions[name] = subprocess.check_output(argv, text=True).strip()
    write(root / "versions.json", versions)
    summary = {"release_acceptance": False, "parasitics": "global-routing estimates",
               "corners": {}, "passed": False}
    write(root / "timing-gate.json", summary)
    for corner in CORNERS:
        cfg = dict(config, PNR_CORNERS=[corner], STA_CORNERS=[corner], DEFAULT_CORNER=corner,
                   STA_EXTRA_CORNER_TCL_FILE="/closure/audit.tcl")
        cfg.pop("meta", None)
        directory = root / "timing" / corner
        try:
            flow = CornerTiming(cfg, design_dir=str(root), pdk_root="/pdk", pdk="ihp-sg13cmos5l")
            flow.start(with_initial_state=State.load(state), _force_run_dir=str(directory))
            step = timing_step(directory)
            # Only the raw metrics from this new process are used for the gate.
            metrics = read(step / "or_metrics_out.json")
            issues = timing_issues(metrics, corner)
            with (root / f"annotation-{corner}.log").open("w") as log:
                subprocess.run(["openroad", "-exit", "-no_splash", "-python",
                                "/closure/v2_closure_annotation.py", state["odb"],
                                str(step / "checks.rpt"), str(step / "annotation.json")],
                               check=True, stdout=log, stderr=subprocess.STDOUT)
            annotation = read(step / "annotation.json")
            if not annotation["passed"]:
                issues.append("parasitic annotation audit failed; see annotation.json")
            summary["corners"][corner] = {"issues": issues, "metrics": metrics,
                                            "annotation": annotation}
        except Exception as error:
            summary["corners"][corner] = {"issues": [f"analysis failed: {error}"]}
        write(root / "timing-gate.json", summary)
    summary["passed"] = all(not value["issues"] for value in summary["corners"].values())
    summary["artifact_sha256"] = {
        str(path.relative_to(root)): sha(path) for path in sorted((root / "timing").rglob("*"))
        if path.is_file()}
    write(root / "timing-gate.json", summary)
    return 0 if summary["passed"] else 2


def prepare(args):
    try:
        from .validate_local import source_state
    except ImportError:
        from validate_local import source_state
    sweep, predecessor, pdk, tt, destination = (
        p.resolve() for p in (args.sweep, args.predecessor, args.pdk_root, args.tt_root, args.output))
    mounts = {"/trial": sweep, "/work": predecessor, "/work/tt": tt, "/pdk": pdk}
    verify_checkout(pdk, PDK_REV)
    verify_checkout(tt, TT_REV)
    config, state, artifacts = verify_inputs(ROOT, sweep, predecessor, mounts)
    image = json.loads(output("docker", "image", "inspect", IMAGE))[0]
    docker_info = json.loads(output("docker", "info", "--format", "{{json .}}"))
    if args.name in output("docker", "ps", "-a", "--format", "{{.Names}}").splitlines():
        raise ValueError(f"container name already exists: {args.name}")
    destination.mkdir(parents=True, exist_ok=False)
    config.update(DESIGN_DIR="/closure", DRT_SAVE_SNAPSHOTS=True)
    state["metrics"] = {}  # Deliberately drop all inherited cumulative metrics.
    write(destination / "config.json", config)
    write(destination / "state.json", state)
    for source in (Path(__file__), ROOT / "tools/local_flow.py",
                   ROOT / "tools/v2_closure_annotation.py",
                   ROOT / "physical/v2/closure/audit.tcl"):
        shutil.copy2(source, destination / source.name)
    command = ["docker", "run", "--detach", "--name", args.name, "--network", "none",
               "-v", f"{destination}:/closure", "-w", "/closure"]
    for target, host in mounts.items():
        command += ["-v", f"{host}:{target}:ro"]
    command += [IMAGE, "python", "/closure/v2_closure.py", "--inside"]
    manifest = {"schema": 1, "release_acceptance": False, **source_state(ROOT),
                "image": IMAGE, "image_id": image["Id"], "pdk_commit": PDK_REV,
                "support_tools_commit": TT_REV, "container_name": args.name,
                "mounts": {key: str(value) for key, value in mounts.items()},
                "predecessor_artifact_sha256": artifacts,
                "sweep_manifest_sha256": sha(sweep / "manifest.json"),
                "checkpoint_sha256": sha(sweep / CHECKPOINT),
                "prepared_sha256": {p.name: sha(p) for p in destination.iterdir() if p.is_file()},
                "disk_free_bytes": shutil.disk_usage(destination).free,
                "docker_memory_bytes": docker_info["MemTotal"], "command": command}
    write(destination / "manifest.json", manifest)
    if args.launch:
        manifest["container_id"] = output(*command)
        write(destination / "manifest.json", manifest)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sweep", type=Path)
    parser.add_argument("--predecessor", type=Path)
    parser.add_argument("--pdk-root", type=Path)
    parser.add_argument("--tt-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--name", default="v2-closure-hold005-timing")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        root = Path("/closure")
        stop = threading.Event()
        def monitor():
            while not stop.wait(30):
                sample_resources(root)
        sample_resources(root)
        thread = threading.Thread(target=monitor, daemon=True)
        thread.start()
        try:
            with (root / "flow.log").open("w", buffering=1) as log:
                # Keep a host-readable log even if Docker's VM is restarted.
                from contextlib import redirect_stdout, redirect_stderr
                with redirect_stdout(log), redirect_stderr(log):
                    return inside()
        finally:
            stop.set()
            thread.join()
            sample_resources(root)
    if any(getattr(args, key) is None for key in ("sweep", "predecessor", "pdk_root", "tt_root", "output")):
        parser.error("--sweep, --predecessor, --pdk-root, --tt-root and --output are required")
    print(prepare(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
