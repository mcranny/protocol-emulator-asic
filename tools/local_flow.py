"""Run isolated local CMOS5L optimization or GDS builds using the pinned image.

Local results are development evidence, not the complete V1 release gate.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

IMAGE = "ghcr.io/librelane/librelane@sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a"
PDK_REV = "2bbec755dc67ca3db0261c3d6163e15735d66710"
TT_REV = "f6bf5c587fba4a4a8abd4c0a03234fccfbf6e61e"


def output(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def verify_checkout(path, expected):
    if output("git", "rev-parse", "HEAD", cwd=path) != expected:
        raise ValueError(f"{path}: expected revision {expected}")
    if output("git", "status", "--porcelain", "--untracked-files=no", cwd=path):
        raise ValueError(f"{path}: modified dependency checkout")


def user_config(project, tile_sizes):
    """Match pinned tt-support-tools Project.create_user_config for this design."""
    if project["language"] != "Verilog" or project["tiles"] != "6x4":
        raise ValueError("Local runner supports the V1 Verilog/6x4 configuration only")
    if project["clock_hz"] != 25_000_000:
        raise ValueError("V1 requires 25 MHz")
    sources = project["source_files"]
    if not sources or any(Path(s).name != s or not s.endswith(".v") for s in sources):
        raise ValueError("Expected simple Verilog source filenames")
    return {
        "DESIGN_NAME": project["top_module"],
        "VERILOG_FILES": [f"dir::{s}" for s in sources],
        "DIE_AREA": tile_sizes["6x4"],
        "FP_DEF_TEMPLATE": "dir::../tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def",
        "VDD_PIN": "VPWR", "GND_PIN": "VGND", "RT_MAX_LAYER": "Metal4",
    }


def inside(stage, threads):
    import yaml  # Provided by the pinned container, not needed on the host.

    project = yaml.safe_load(Path("info.yaml").read_text())["project"]
    sizes = yaml.safe_load(Path("tt/tech/ihp-sg13cmos5l/tile_sizes.yaml").read_text())
    config = json.loads(Path("src/config.json").read_text())
    if config["CLOCK_PERIOD"] != 40:
        raise ValueError("V1 requires a 40 ns clock period")
    config.update(user_config(project, sizes))
    Path("src/config_merged.json").write_text(json.dumps(config, indent=2) + "\n")
    for command in (("openroad", "-version"), ("yosys", "-V"),
                    (sys.executable, "-m", "librelane", "--version")):
        print(output(*command), flush=True)
    command = [sys.executable, "-m", "librelane", "--manual-pdk", "--pdk-root", "/pdk",
               "--pdk", "ihp-sg13cmos5l", "--run-tag", "local", "-j", str(threads)]
    if stage == "route":
        command += ["--to", "OpenROAD.STAPostPNR"]
    command += ["src/config_merged.json"]
    return subprocess.call(command)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--pdk-root", type=Path)
    parser.add_argument("--tt-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--stage", choices=("route", "full"), default="route")
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--inside", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")
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
    files = [source / "info.yaml", source / "src/config.json", *sorted((source / "src").glob("*.v"))]
    manifest = {
        "source_commit": output("git", "rev-parse", "HEAD", cwd=source),
        "source_dirty": bool(output("git", "status", "--porcelain", cwd=source)),
        "pdk_commit": PDK_REV, "support_tools_commit": TT_REV,
        "image": IMAGE, "image_id": image["Id"], "architecture": image["Architecture"],
        "stage": args.stage, "release_acceptance": False, "files": {},
    }
    for path in files:
        relative = path.relative_to(source)
        shutil.copy2(path, destination / relative)
        manifest["files"][str(relative)] = hashlib.sha256(path.read_bytes()).hexdigest()
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
    manifest["exit_code"] = result
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Build exit code: {result}. Local output does not establish release acceptance.")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
