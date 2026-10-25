"""Run isolated local CMOS5L optimization or GDS builds using the pinned image.

Local results are development evidence, not the complete V1 release gate.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys

IMAGE = "ghcr.io/librelane/librelane@sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a"
PDK_REV = "2bbec755dc67ca3db0261c3d6163e15735d66710"
TT_REV = "d66cf179e7bc4d296362ab7e2e3b344dc3c4f665"


def evidence_key(relative: Path) -> str:
    """Return a manifest key with POSIX separators.

    ``str(path)`` on Windows uses backslashes, so the same file would hash
    under a different key than on Linux.
    """
    return relative.as_posix()


def output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def verify_checkout(path, expected):
    if output("git", "rev-parse", "HEAD", cwd=path) != expected:
        raise ValueError(f"{path}: expected revision {expected}")
    if output("git", "status", "--porcelain", "--untracked-files=no", cwd=path):
        raise ValueError(f"{path}: modified dependency checkout")


V3_TOP = "tt_um_mcranny_protocol_emulator_v3"

# Per-profile layout inside the run directory. LibreLane resolves ``dir::``
# against the config's own directory, so the V3 config, which sits two levels
# below the run root, reaches the shared src/ and tt/ trees through ``../../``.
PROFILES = {
    "v1": {"config": "src/config.json", "period": 40, "root": "dir::", "companions": ()},
    "v3": {"config": "physical/v3/config.json", "period": 20.833, "root": "dir::../../",
           "companions": ("physical/v3/pdn_cfg.tcl",)},
}


def profile_name(project):
    """Return the profile an ``info.yaml`` project selects."""
    if project["tiles"] == "8x4" and project["top_module"] == V3_TOP:
        return "v3"
    return "v1"


def _check_sources(sources):
    if not sources or any(Path(s).name != s or not s.endswith(".v") for s in sources):
        raise ValueError("Expected simple Verilog source filenames")


def user_config(project, tile_sizes):
    """Match pinned tt-support-tools Project.create_user_config for this design."""
    if profile_name(project) == "v3":
        if project["language"] != "Verilog":
            raise ValueError("V3 requires Verilog")
        if project["clock_hz"] != 48_000_000:
            raise ValueError("V3 requires 48 MHz")
        _check_sources(project["source_files"])
        root = PROFILES["v3"]["root"]
        return {
            "DESIGN_NAME": project["top_module"],
            "VERILOG_FILES": [f"{root}src/{s}" for s in project["source_files"]],
            "DIE_AREA": tile_sizes["8x4"],
            "FP_DEF_TEMPLATE": f"{root}tt/tech/ihp-sg13cmos5l/def/tt_block_8x4_pgvdd.def",
            "VDD_PIN": "VPWR", "GND_PIN": "VGND", "RT_MAX_LAYER": "Metal4",
        }
    if project["language"] != "Verilog" or project["tiles"] != "6x4":
        raise ValueError("Local runner supports the V1 Verilog/6x4 and V3 8x4 configurations only")
    if project["clock_hz"] != 25_000_000:
        raise ValueError("V1 requires 25 MHz")
    _check_sources(project["source_files"])
    return {
        "DESIGN_NAME": project["top_module"],
        "VERILOG_FILES": [f"dir::{s}" for s in project["source_files"]],
        "DIE_AREA": tile_sizes["6x4"],
        "FP_DEF_TEMPLATE": "dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def",
        "VDD_PIN": "VPWR", "GND_PIN": "VGND", "RT_MAX_LAYER": "Metal4",
    }


def inside(stage, threads):
    import yaml  # Provided by the pinned container, not needed on the host.

    project = yaml.safe_load(Path("info.yaml").read_text())["project"]
    sizes = yaml.safe_load(Path("tt/tech/ihp-sg13cmos5l/tile_sizes.yaml").read_text())
    name = profile_name(project)
    profile = PROFILES[name]
    config_path = Path(profile["config"])
    for companion in profile["companions"]:
        if not Path(companion).is_file():
            raise ValueError(f"{name} requires {companion} next to its config")
    config = json.loads(config_path.read_text())
    if config["CLOCK_PERIOD"] != profile["period"]:
        raise ValueError(f"{name} requires a {profile['period']} ns clock period")
    config.update(user_config(project, sizes))
    # Pinned flow otherwise stringifies an unset thread count as "None".
    config["OPENROAD_THREADS"] = threads
    config["STA_THREADS"] = threads
    merged = config_path.with_name("config_merged.json")
    merged.write_text(json.dumps(config, indent=2) + "\n")
    for command in (("openroad", "-version"), ("yosys", "-V"),
                    (sys.executable, "-m", "librelane", "--version")):
        print(output(*command), flush=True)
    command = [sys.executable, str(Path(__file__).resolve()), "--librelane",
               "--manual-pdk", "--pdk-root", "/pdk",
               "--pdk", "ihp-sg13cmos5l", "--run-tag", "local", "-j", str(threads)]
    if stage == "route":
        command += ["--to", "OpenROAD.STAPostPNR"]
    elif stage == "pdn":
        command += ["--to", "OpenROAD.GeneratePDN"]
    elif stage == "grt":
        command += ["--to", "OpenROAD.STAMidPNR-3"]
    command += [merged.as_posix()]
    return subprocess.call(command)


_JSON_ESCAPE = re.compile(r'\\(["\\/bfnrt]|u[0-9a-fA-F]{4})|\\')


def repair_json_escapes(text):
    """Double each backslash that does not begin a valid JSON escape."""
    return _JSON_ESCAPE.sub(lambda match: match.group(0) if match.group(1) else "\\\\", text)


def run_librelane(arguments):
    """Run the LibreLane command line in this process with the LVS report repaired.

    Netgen copies escaped Verilog names, such as the SRAM supply pin ``\\VDD!``,
    into its JSON report without escaping the backslash, and the pinned
    LibreLane stops on that report after LVS has finished.
    """
    import runpy
    from types import SimpleNamespace

    from librelane.steps import netgen

    netgen.json = SimpleNamespace(**{
        **{name: getattr(json, name) for name in json.__all__},
        "loads": lambda text, **options: json.loads(repair_json_escapes(text), **options),
    })
    sys.argv = ["librelane", *arguments]
    try:
        runpy.run_module("librelane", run_name="__main__", alter_sys=True)
    except SystemExit as stop:
        if stop.code is None or isinstance(stop.code, int):
            return stop.code or 0
        print(stop.code, file=sys.stderr)
        return 1
    return 0


def metrics_path(destination):
    """Return the final metrics file of whichever profile's run is present.

    LibreLane writes ``runs/`` next to the config it was given.
    """
    candidates = [destination / Path(p["config"]).parent / "runs/local/final/metrics.json"
                  for p in PROFILES.values()]
    return next((path for path in candidates if path.is_file()), candidates[0])


def route_issues(destination):
    """Check the shortened flow's metrics; deliberately not release acceptance."""
    path = metrics_path(destination)
    try:
        metrics = json.loads(path.read_text())
    except (OSError, ValueError) as error:
        return [f"missing/invalid routed metrics: {error}"]
    if not isinstance(metrics, dict):
        return ["invalid routed metrics object"]
    zero = ["route__drc_errors", "route__antenna_violation__count"]
    slack = []
    for rule in ("slew", "fanout", "cap"):
        base = f"design__max_{rule}_violation__count"
        zero.append(base)
        zero.extend(f"{base}__corner:{corner}" for corner in
                    ("nom_fast_1p32V_m40C", "nom_slow_1p08V_125C", "nom_typ_1p20V_25C"))
    for corner in ("nom_fast_1p32V_m40C", "nom_slow_1p08V_125C", "nom_typ_1p20V_25C"):
        zero.append(f"timing__unannotated_net_filtered__count__corner:{corner}")
        for kind in ("setup", "hold"):
            for suffix in ("", "_r2r"):
                slack.append(f"timing__{kind}{suffix}__ws__corner:{corner}")
                zero.append(f"timing__{kind}{suffix}_vio__count__corner:{corner}")
    issues = []
    for key in zero + slack:
        value = metrics.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            issues.append(f"missing/invalid metric: {key}")
        elif (key in zero and value != 0) or (key in slack and not 0 <= value < 1e6):
            issues.append(f"{key} = {value}")
    return issues


def run_issues(stage, result, destination):
    """Return the issues recorded for a finished container run.

    LibreLane writes the final metrics before it stops on deferred errors, so
    a failed route or full run still reports which checks it missed. The
    ``pdn`` and ``grt`` stages stop before any routed metrics exist.
    """
    issues = [] if result == 0 else ["physical tool execution failed"]
    if stage in ("route", "full"):
        issues += route_issues(destination)
    return issues


def modified_files(source, relatives):
    """Return the copied files whose contents differ from the source commit."""
    changed = output("git", "diff", "--name-only", "HEAD", "--", *relatives, cwd=source)
    new = output("git", "ls-files", "--others", "--", *relatives, cwd=source)
    return sorted({*changed.splitlines(), *new.splitlines()} - {""})


def main():
    if sys.argv[1:2] == ["--librelane"]:
        return run_librelane(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--pdk-root", type=Path)
    parser.add_argument("--tt-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--check-only", action="store_true", help="Assess existing local routed metrics without rebuilding")
    parser.add_argument("--stage", choices=("route", "pdn", "grt", "full"), default="route")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")
    if args.check_only:
        if args.output is None:
            parser.error("--check-only requires --output")
        issues = route_issues(args.output)
        print(json.dumps({"route_issues": issues, "release_acceptance": False}, indent=2))
        return 2 if issues else 0
    if args.inside:
        return inside(args.stage, args.threads)
    if any(value is None for value in (args.pdk_root, args.tt_root, args.output)):
        parser.error("--pdk-root, --tt-root, and --output are required")
    source, pdk, tt, destination = (p.resolve() for p in
                                   (args.source, args.pdk_root, args.tt_root, args.output))
    verify_checkout(pdk, PDK_REV)
    verify_checkout(tt, TT_REV)
    # Validate tools before allocating output. No network access during the build.
    image = json.loads(output("docker", "image", "inspect", IMAGE))[0]
    output("docker", "info", "--format", "{{.ServerVersion}}")
    destination.mkdir(parents=True, exist_ok=False)  # Never overwrite a previous run.
    (destination / "src").mkdir()
    # Headers are pulled in by `include, so they travel with the sources. Each
    # profile's config and companions are copied when present; the container
    # picks the profile from info.yaml and refuses a missing config.
    files = [source / "info.yaml",
             *sorted([*(source / "src").glob("*.v"), *(source / "src").glob("*.vh")])]
    for profile in PROFILES.values():
        if (source / profile["config"]).is_file():
            files += [source / profile["config"], *(source / c for c in profile["companions"])]
    if (source / "candidate-inputs.json").is_file():
        files.append(source / "candidate-inputs.json")
    manifest = {
        "source_commit": output("git", "rev-parse", "HEAD", cwd=source),
        "source_dirty": bool(output("git", "status", "--porcelain", cwd=source)),
        "pdk_commit": PDK_REV, "support_tools_commit": TT_REV,
        "image": IMAGE, "image_id": image["Id"], "architecture": image["Architecture"],
        "stage": args.stage, "release_acceptance": False, "files": {},
    }
    for path in files:
        relative = path.relative_to(source)
        (destination / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination / relative)
        manifest["files"][evidence_key(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["modified_files"] = modified_files(source, list(manifest["files"]))
    runner = Path(__file__).resolve()
    manifest["runner_sha256"] = hashlib.sha256(runner.read_bytes()).hexdigest()
    manifest_path = destination / "local-evidence.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    command = ["docker", "run", "--rm", "--network", "none",
               "-v", f"{destination}:/work", "-v", f"{pdk}:/pdk:ro",
               "-v", f"{tt}:/work/tt:ro", "-v", f"{runner}:/runner.py:ro",
               "-w", "/work", IMAGE, "python", "/runner.py", "--inside",
               "--stage", args.stage, "--threads", str(args.threads)]
    print(f"Local {args.stage} build: {destination}\nLog: {destination / 'flow.log'}", flush=True)
    with (destination / "flow.log").open("w") as log:
        result = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    manifest["tool_exit_code"] = result
    issues = run_issues(args.stage, result, destination)
    manifest["route_issues"] = issues
    if result == 0 and issues:
        result = 2
    if issues:
        print("\n".join(issues), flush=True)
    manifest["exit_code"] = result
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Build exit code: {result}. Local output does not establish release acceptance.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
