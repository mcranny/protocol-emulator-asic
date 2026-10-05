"""Attach a corrected observer to a running trial without restarting OpenROAD.

The original supervisor still records resources. This observer writes a separate
evidence directory and applies the same stop policy using the flushed flow log.
"""
import argparse
import os
from pathlib import Path
import signal
import time

try:
    from .v2_closure import read, write
    from .v2_route_monitor import sample
    from .v2_route_trial import resource_reason
except ImportError:
    from v2_closure import read, write
    from v2_route_monitor import sample
    from v2_route_trial import resource_reason


def observe(root, destination, process_group):
    command = Path(f"/proc/{process_group}/cmdline").read_bytes().split(b"\0")
    if b"librelane" not in command or os.getpgid(process_group) != process_group:
        raise ValueError("target is not the running LibreLane process group")
    manifest = read(root / "manifest.json")
    record = {"phase": "observing", "process_group": process_group, "stop_reason": None,
              "started_unix": time.time(), "command": [part.decode() for part in command if part]}
    write(destination / "observer-status.json", record)
    while Path(f"/proc/{process_group}").exists():
        resource, progress = sample(root, destination)
        reason = resource_reason(resource, manifest["memory_stop_bytes"])
        if progress["plateau_requires_review"] and progress["completed"][-1]["iteration"] >= 50:
            reason = reason or "late routing plateau: <1% improvement across 10 completed iterations"
        if reason:
            record.update(phase="stopping", stop_reason=reason)
            write(destination / "observer-status.json", record)
            os.killpg(process_group, signal.SIGTERM)
            deadline = time.monotonic() + 20
            while Path(f"/proc/{process_group}").exists() and time.monotonic() < deadline:
                time.sleep(1)
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(30)
    sample(root, destination)
    record.update(phase="finished", finished_unix=time.time())
    write(destination / "observer-status.json", record)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--process-group", type=int, required=True)
    args = parser.parse_args()
    observe(args.root, args.output, args.process_group)
