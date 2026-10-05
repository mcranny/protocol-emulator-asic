"""Collect DRT convergence, spatial DRC summaries and container resource evidence."""
from collections import Counter
import json
import math
from pathlib import Path
import re
import shutil
import time


def iterations(log):
    completed, current, run = [], None, -1
    for line in log.splitlines():
        match = re.search(r"Start (\d+)\w+ (.*?) iteration\.", line)
        if match:
            if int(match[1]) == 0:
                run += 1
            current = {"run": run, "iteration": int(match[1]), "kind": match[2]}
        match = re.search(r"Number of violations = (\d+)\.", line)
        if match and current is not None:
            completed.append(dict(current, violations=int(match[1])))
            current = None
    return {"completed": completed, "current": current}


def drc_summary(report):
    """Strictly parse three-line OpenROAD records; incomplete files are not zero."""
    lines = [line.strip() for line in report.splitlines() if line.strip()]
    if len(lines) % 3:
        raise ValueError("incomplete DRC record")
    layer_counts, types, regions, power_regions = Counter(), Counter(), Counter(), Counter()
    conflicts = []
    power = {"net:VPWR", "net:VGND", "net:VDD", "net:VSS"}
    for offset in range(0, len(lines), 3):
        kind = re.fullmatch(r"violation type: (.+)", lines[offset])
        sources = re.fullmatch(r"srcs: (.+)", lines[offset + 1])
        bbox = re.fullmatch(r"bbox = \(([^,]+), ([^)]+)\) - \(([^,]+), ([^)]+)\) on Layer (\S+)",
                            lines[offset + 2])
        if not (kind and sources and bbox):
            raise ValueError("invalid DRC record")
        xy = [float(bbox[index]) for index in range(1, 5)]
        if not all(math.isfinite(value) for value in xy):
            raise ValueError("invalid DRC coordinates")
        layer_counts[bbox[5]] += 1
        types[kind[1]] += 1
        # A fixed 100 um grid supports comparisons without implying exact density.
        region = f"{math.floor((xy[0] + xy[2]) / 200) * 100},{math.floor((xy[1] + xy[3]) / 200) * 100}"
        regions[region] += 1
        nets = set(sources[1].split())
        if nets & power and any(net.startswith("net:") and net not in power for net in nets):
            conflicts.append({"type": kind[1], "layer": bbox[5], "sources": sorted(nets), "bbox_um": xy})
            power_regions[region] += 1
    return {"count": len(lines) // 3, "by_layer": dict(layer_counts), "by_type": dict(types),
            "regions_100um": dict(regions), "signal_to_power_count": len(conflicts),
            "signal_to_power_regions_100um": dict(power_regions), "signal_to_power": conflicts}


def stalled(progress, window=10):
    """Flag a plateau for review; do not mistake a new antenna pass for one."""
    entries = progress["completed"]
    if len(entries) < window:
        return False
    recent = entries[-window:]
    if recent[-1]["iteration"] < 20 or len({entry["run"] for entry in recent}) != 1:
        return False
    best = min(entry["violations"] for entry in recent)
    return best > 0 and recent[0]["violations"] - best < max(1, recent[0]["violations"] * 0.01)


def sample(root):
    sample = {"unix_time": time.time(), "disk_free_bytes": shutil.disk_usage(root).free}
    for name in ("memory.current", "memory.peak", "memory.swap.current", "memory.swap.peak", "memory.events"):
        path = Path("/sys/fs/cgroup") / name
        if path.exists():
            sample[name] = path.read_text().strip()
    processes = []
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            data = dict(line.split(":", 1) for line in path.read_text().splitlines() if ":" in line)
            if "openroad" in data.get("Name", "").lower():
                processes.append({key: data[key].strip() for key in ("Name", "Pid", "VmRSS", "VmHWM", "VmSwap")
                                  if key in data})
        except (FileNotFoundError, ProcessLookupError):
            continue
    sample["openroad_processes"] = processes
    snapshots = [{"path": str(path.relative_to(root)), "bytes": path.stat().st_size}
                 for path in sorted(root.rglob("drt_iter*.odb")) if path.is_file()]
    sample["snapshot_count"] = len(snapshots)
    sample["snapshot_bytes"] = sum(entry["bytes"] for entry in snapshots)
    (root / "snapshots.json").write_text(json.dumps(snapshots, indent=2) + "\n")
    with (root / "resources.jsonl").open("a") as stream:
        stream.write(json.dumps(sample) + "\n")
    logs = list(root.glob("runs/*/*-openroad-detailedrouting/openroad-detailedrouting.log"))
    progress = iterations(logs[0].read_text()) if len(logs) == 1 else {"completed": [], "current": None}
    progress["plateau_requires_review"] = stalled(progress)
    progress["sampled_at"] = sample["unix_time"]
    (root / "route-progress.json").write_text(json.dumps(progress, indent=2) + "\n")
    reports = root / "drc-summaries"
    reports.mkdir(exist_ok=True)
    for path in root.glob("runs/*/*-openroad-detailedrouting/drt-run-*/*.drc*.rpt"):
        # Skip files still being written. Once summarized, each immutable iteration
        # report remains available in full next to its spatial summary.
        if time.time() - path.stat().st_mtime < 5:
            continue
        target = reports / (path.parent.name + "-" + path.name + ".json")
        if target.exists():
            continue
        try:
            summary = drc_summary(path.read_text())
            summary["report"] = str(path.relative_to(root))
            target.write_text(json.dumps(summary, indent=2) + "\n")
        except ValueError:
            continue
    return sample, progress
