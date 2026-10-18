"""Serve a read-only, live local view of a V2 routing trial on loopback only."""
import argparse
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
from pathlib import Path
import subprocess
import threading
import time

try:
    from .v2_route_monitor import iterations, stalled
except ImportError:
    from v2_route_monitor import iterations, stalled

PAGE = Path(__file__).resolve().parents[1] / "physical/v2/closure/observer.html"


def antenna_progress(log, baseline):
    checks = list(re.finditer(r'Found (\d+) net violations\.', log))
    insertions = list(re.finditer(r'Inserted (\d+) diodes\.', log))
    return dict(baseline_nets=baseline,
                last_checked_nets=int(checks[-1][1]) if checks else None,
                inserted_diodes=sum(int(m[1]) for m in insertions),
                check_pending=bool(insertions) and (not checks or insertions[-1].start() > checks[-1].start()))


def read_json(path, default=None):
    # The observer writes small JSON files every 30 seconds. Retry a partial
    # write rather than briefly presenting it as a completed/empty result.
    for attempt in range(3):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            if attempt < 2:
                time.sleep(0.01)
    return default


def tail(path, limit=65536):
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            length = stream.tell()
            stream.seek(max(0, length - limit))
            return stream.read().decode("utf-8", errors="replace")
    except OSError:
        return ""


def resource_history(root, observer):
    records = {}
    for directory in dict.fromkeys((root, observer)):
        for line in tail(directory / "resources.jsonl", 4 * 1024**2).splitlines():
            try:
                record = json.loads(line)
                records[record["unix_time"]] = record
            except (ValueError, KeyError, TypeError):
                continue  # Ignore only an incomplete first/last JSONL record.
    return [records[timestamp] for timestamp in sorted(records)]


def snapshot(root, docker_state, now=None):
    now = time.time() if now is None else now
    manifest = read_json(root / "manifest.json", {})
    observers = [path for path in root.glob("observer-*") if (path / "observer-status.json").exists()]
    observer = max(observers, key=lambda p: p.stat().st_mtime) if observers else root
    history = resource_history(root, observer)
    resource = history[-1] if history else {}
    resource_age = max(0, now - resource["unix_time"]) if resource else None
    flow = root / "flow.log"
    flow_text = ""
    try:
        flow_text = flow.read_text()
        progress = iterations(flow_text)
        log_age = max(0, now - flow.stat().st_mtime)
    except OSError:
        progress = {"completed": [], "current": None}
        log_age = None
    progress["plateau_requires_review"] = stalled(progress)
    status = read_json(root / "route-status.json", {})
    observer_status = read_json(observer / "observer-status.json", {})
    observer_progress = read_json(observer / "route-progress.json", {})
    observer_sampled = observer_progress.get("sampled_at")
    observer_age = max(0, now - observer_sampled) if observer_sampled is not None else None
    drc_files = sorted((observer / "drc-summaries").glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    drc = next((value for path in drc_files if (value := read_json(path)) is not None), None)
    if drc:
        # The full per-violation list remains in the evidence directory. The live
        # page needs aggregate counts and regions, not thousands of net names.
        drc = {key: value for key, value in drc.items() if key != "signal_to_power"}
    series = [{"time": item["unix_time"], "bytes": int(item["memory.current"])}
              for item in history if "memory.current" in item]
    if len(series) > 400:
        stride = (len(series) + 398) // 399
        series = series[::stride] + ([series[-1]] if series[::stride][-1] != series[-1] else [])
    return {"fetched_at": now, "container_name": manifest.get("container_name"),
            "run_directory": str(root), "threads": manifest.get("threads", 2),
            "routing_search": manifest.get("routing_search", {"seed": 42, "swaps_per_net": 0}),
            "repair_mode": manifest.get("repair_mode"),
            "baseline_violations": manifest.get("baseline_violations"),
            "antenna": antenna_progress(flow_text, manifest.get("baseline_antenna_nets"))
                if manifest.get("antenna_checkpoint") else None,
            "docker": docker_state, "status": status, "observer": observer_status,
            "observer_age_seconds": observer_age,
            "observer_stale": bool(observers) and (observer_age is None or observer_age > 90),
            "progress": progress, "resource": resource, "resource_age_seconds": resource_age,
            "resource_stale": resource_age is None or resource_age > 90,
            "log_age_seconds": log_age, "memory_history": series,
            "memory_stop_bytes": manifest.get("memory_stop_bytes"),
            "snapshots": read_json(observer / "snapshots.json", []), "drc": drc,
            "log_tail": "\n".join(line.rstrip() for line in tail(flow).splitlines()[-65:]),
            "observer_directory": observer.name, "release_acceptance": False}


class DockerStatus:
    def __init__(self, name):
        self.name, self.updated, self.cached = name, 0, {}
        self.lock = threading.Lock()

    def get(self):
        with self.lock:
            if time.monotonic() - self.updated >= 10:
                try:
                    result = subprocess.check_output(
                        ["docker", "inspect", self.name, "--format", "{{json .State}}"],
                        text=True, stderr=subprocess.STDOUT, timeout=3)
                    self.cached = json.loads(result)
                except (OSError, ValueError, subprocess.SubprocessError) as error:
                    self.cached = {"Status": "unknown", "inspection_error": str(error)}
                self.updated = time.monotonic()
            return self.cached


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, root, docker, **kwargs):
        self.root, self.docker = root, docker
        super().__init__(*args, **kwargs)

    def do_GET(self):
        if self.headers.get("Host", "").split(":")[0] not in ("127.0.0.1", "localhost"):
            self.send_error(403)
            return
        route = self.path.split("?", 1)[0]
        if route == "/":
            content, mime = PAGE.read_bytes(), "text/html; charset=utf-8"
        elif route == "/api/status":
            content = json.dumps(snapshot(self.root, self.docker.get()), allow_nan=False).encode()
            mime = "application/json"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'self'")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    root = args.run.resolve()
    manifest = read_json(root / "manifest.json")
    if not manifest or not manifest.get("container_name"):
        parser.error("--run must contain a routing manifest with a container name")
    handler = partial(Handler, root=root, docker=DockerStatus(manifest["container_name"]))
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    url = f"http://127.0.0.1:{server.server_port}"
    print(url, flush=True)
    (root / "viewer.json").write_text(json.dumps({"url": url, "read_only": True}, indent=2) + "\n")
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
