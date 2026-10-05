"""Launch one retained Metal2-Metal4 DRT trial after the fresh V2 timing gate.

The supervisor records resources every 30 seconds and stops the child process
group on material resource pressure or a sustained late-iteration plateau.
It preserves existing logs and snapshots; an interrupt is not a new checkpoint.
"""
import argparse
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import time

try:
    from . import v2_closure as closure
    from .v2_route_monitor import sample
except ImportError:
    import v2_closure as closure
    from v2_route_monitor import sample

ROOT = Path(__file__).resolve().parents[1]
MIN_DISK = 20 * 1024**3
MEMORY_RESERVE = 1024**3


def verify_timing(root, source=None):
    manifest = closure.read(root / "manifest.json")
    for group in (manifest["prepared_sha256"], closure.read(root / "timing-gate.json")["artifact_sha256"]):
        if not group:
            raise ValueError("missing timing artifact hashes")
        for name, expected in group.items():
            path = closure.mapped_path("/timing/" + name, {"/timing": root})
            if closure.sha(path) != expected:
                raise ValueError(f"timing evidence changed: {name}")
    for key, expected in (("image", closure.IMAGE), ("pdk_commit", closure.PDK_REV),
                          ("support_tools_commit", closure.TT_REV)):
        if manifest.get(key) != expected:
            raise ValueError(f"timing dependency mismatch: {key}")
    if source is not None:
        for name in closure.RTL:
            if closure.sha(source / "src" / name) != manifest["inputs"]["src/" + name]:
                raise ValueError(f"RTL changed after timing: {name}")
    gate = closure.read(root / "timing-gate.json")
    if gate.get("passed") is not True or set(gate.get("corners", {})) != set(closure.CORNERS):
        raise ValueError("fresh timing gate has not passed at all corners")
    for corner in closure.CORNERS:
        step = closure.timing_step(root / "timing" / corner)
        for name in ("or_metrics_out.json", "annotation.json", "checks.rpt"):
            if str((step / name).relative_to(root)) not in gate["artifact_sha256"]:
                raise ValueError(f"unhashed required report: {corner}/{name}")
        raw = closure.read(step / "or_metrics_out.json")
        annotation = closure.read(step / "annotation.json")
        if (closure.timing_issues(raw, corner) or gate["corners"][corner].get("issues") != []
                or raw != gate["corners"][corner]["metrics"]
                or annotation != gate["corners"][corner]["annotation"]):
            raise ValueError(f"invalid fresh corner evidence: {corner}")
        if (annotation.get("passed") is not True or annotation.get("connected_unannotated_drivers") != []
                or annotation.get("unknown_drivers") != []
                or type(annotation.get("partially_unannotated_driver_count")) is not int
                or annotation["partially_unannotated_driver_count"] != 0
                or annotation.get("unannotated_drivers") != annotation.get("unconnected_drivers")):
            raise ValueError(f"invalid annotation evidence: {corner}")
    config = closure.read(root / "config.json")
    for key, expected in closure.FIXED.items():
        if config.get(key) != expected:
            raise ValueError(f"timing configuration mismatch: {key}")
    state = closure.read(root / "state.json")
    return manifest, config, state


def resource_reason(resource, memory_limit):
    if resource["disk_free_bytes"] < MIN_DISK:
        return "remaining disk below 20 GiB"
    if int(resource.get("memory.current", 0)) > memory_limit:
        return "container memory entered the reserved 1 GiB of VM headroom"
    events = dict(line.split() for line in resource.get("memory.events", "").splitlines())
    if int(events.get("oom", 0)) or int(events.get("oom_kill", 0)):
        return "container recorded an OOM event"
    if int(resource.get("memory.swap.current", 0)) > 512 * 1024**2:
        return "container swap exceeded 512 MiB"
    return None


def extracted_issues(metrics):
    issues = []
    zeros = ["route__drc_errors", "route__antenna_violation__count"]
    for corner in closure.CORNERS:
        # The mid-PnR audit markers are not applicable to extracted STA.
        issues += closure.timing_issues(dict(metrics, closure__global_route_parasitics=1,
                                             closure__propagated_clock=1), corner)
        zeros.append(f"timing__unannotated_net_filtered__count__corner:{corner}")
    for key in zeros:
        if type(metrics.get(key)) not in (int, float) or metrics[key] != 0:
            issues.append(f"missing/invalid/nonzero routed metric: {key}")
    return issues


def stop_child(child):
    os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def inside():
    root = Path("/closure")
    manifest = closure.read(root / "manifest.json")
    for name, expected in manifest["prepared_sha256"].items():
        if closure.sha(root / name) != expected:
            raise ValueError(f"prepared routing input changed: {name}")
    verify_timing(Path("/timing"))
    for name, expected in manifest["predecessor_artifact_sha256"].items():
        if closure.sha(name) != expected:
            raise ValueError(f"predecessor artifact changed: {name}")
    state = closure.read(root / "state.json")
    with (root / "geometry.log").open("w") as log:
        subprocess.run(["openroad", "-exit", "-no_splash", "-python", "/closure/v2_route_geometry.py",
                        state["odb"], "/closure/geometry.json"], stdout=log, stderr=subprocess.STDOUT, check=True)
    command = [sys.executable, "-m", "librelane", "--manual-pdk", "--pdk-root", "/pdk",
               "--pdk", "ihp-sg13cmos5l", "--run-tag", "route", "-j", "2",
               "--from", "OpenROAD.DetailedRouting", "--to", "OpenROAD.STAPostPNR",
               "--with-initial-state", "/closure/state.json", "/closure/config.json"]
    closure.write(root / "flow-command.json", command)
    status = {"phase": "running", "started_unix": time.time(), "release_acceptance": False,
              "passed": False, "stop_reason": None}
    closure.write(root / "route-status.json", status)
    with (root / "flow.log").open("w") as log:
        child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while True:
                resource, progress = sample(root)
                reason = resource_reason(resource, manifest["memory_stop_bytes"])
                # Give the different router strategies time to operate: only stop
                # after iteration 50 and 10 iterations with <1% best improvement.
                if (progress["plateau_requires_review"] and progress["completed"][-1]["iteration"] >= 50):
                    reason = reason or "late routing plateau: <1% improvement across 10 completed iterations"
                if reason and child.poll() is None:
                    status["stop_reason"] = reason
                    closure.write(root / "route-status.json", status)
                    stop_child(child)
                try:
                    child.wait(timeout=30)
                    break
                except subprocess.TimeoutExpired:
                    pass
        except BaseException:
            if child.poll() is None:
                stop_child(child)
            raise
    sample(root)
    status.update(phase="finished", finished_unix=time.time(), tool_exit_code=child.returncode)
    final = root / "runs/route/final/metrics.json"
    status["issues"] = extracted_issues(closure.read(final)) if final.exists() else ["no completed extracted timing state"]
    if status["stop_reason"]:
        status["issues"].append(status["stop_reason"])
    status["passed"] = child.returncode == 0 and not status["issues"]
    closure.write(root / "route-status.json", status)
    return 0 if status["passed"] else 2


def prepare(args):
    try:
        from .validate_local import source_state
    except ImportError:
        from validate_local import source_state
    timing, destination = args.timing.resolve(), args.output.resolve()
    timing_manifest, config, state = verify_timing(timing, ROOT)
    mounts = {name: Path(path) for name, path in timing_manifest["mounts"].items()}
    closure.verify_checkout(mounts["/pdk"], closure.PDK_REV)
    closure.verify_checkout(mounts["/work/tt"], closure.TT_REV)
    original, checkpoint, artifacts = closure.verify_inputs(ROOT, mounts["/trial"], mounts["/work"], mounts)
    expected_config = dict(original, DESIGN_DIR="/closure", DRT_SAVE_SNAPSHOTS=True)
    if config != expected_config or state != dict(checkpoint, metrics={}):
        raise ValueError("timing evidence does not match selected sweep candidate")
    if artifacts != timing_manifest["predecessor_artifact_sha256"]:
        raise ValueError("checkpoint artifacts changed since timing")
    containers = closure.output("docker", "ps", "-a", "--format", "{{.Names}}")
    if args.name in containers.splitlines():
        raise ValueError(f"container already exists: {args.name}")
    running = closure.output("docker", "ps", "--format", "{{.Names}}")
    if running:
        raise ValueError(f"resource preflight requires no other running containers: {running}")
    info = json.loads(closure.output("docker", "info", "--format", "{{json .}}"))
    if info["MemTotal"] < 4 * 1024**3 or shutil.disk_usage(timing).free < MIN_DISK:
        raise ValueError("insufficient Docker memory or disk for controlled trial")
    image = json.loads(closure.output("docker", "image", "inspect", closure.IMAGE))[0]
    destination.mkdir(parents=True, exist_ok=False)
    config["DRT_SAVE_DRC_REPORT_ITERS"] = 1
    closure.write(destination / "config.json", config)
    closure.write(destination / "state.json", state)
    for name in ("v2_route_trial.py", "v2_route_monitor.py", "v2_route_geometry.py", "v2_closure.py", "local_flow.py"):
        shutil.copy2(ROOT / "tools" / name, destination / name)
    command = ["docker", "run", "--detach", "--name", args.name, "--network", "none",
               "-v", f"{destination}:/closure", "-v", f"{timing}:/timing:ro", "-w", "/closure"]
    for target, host in mounts.items():
        command += ["-v", f"{host}:{target}:ro"]
    command += [closure.IMAGE, "python", "/closure/v2_route_trial.py", "--inside"]
    manifest = {"schema": 1, "release_acceptance": False, **source_state(ROOT),
                "image": closure.IMAGE, "image_id": image["Id"], "pdk_commit": closure.PDK_REV,
                "support_tools_commit": closure.TT_REV, "timing_directory": str(timing),
                "timing_manifest_sha256": closure.sha(timing / "manifest.json"),
                "timing_gate_sha256": closure.sha(timing / "timing-gate.json"),
                "predecessor_artifact_sha256": artifacts, "command": command,
                "container_name": args.name, "docker_memory_bytes": info["MemTotal"],
                "memory_stop_bytes": info["MemTotal"] - MEMORY_RESERVE,
                "disk_free_bytes": shutil.disk_usage(destination).free,
                "prepared_sha256": {p.name: closure.sha(p) for p in destination.iterdir() if p.is_file()}}
    closure.write(destination / "manifest.json", manifest)
    if args.launch:
        manifest["container_id"] = closure.output(*command)
        closure.write(destination / "manifest.json", manifest)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timing", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--name", default="v2-closure-hold005")
    parser.add_argument("--launch", action="store_true")
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.inside:
        return inside()
    if args.timing is None or args.output is None:
        parser.error("--timing and --output are required")
    print(prepare(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
