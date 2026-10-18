"""Run the pinned LibreLane flow with an explicitly recorded routing search order."""
import hashlib
import json
from pathlib import Path
import runpy

PINNED_DRT_SHA256 = "e7e4e4984dff976e96fdf42561b185000bc2a9a3a21553b9bc781cfaef94447f"


def search_script(original, seed, swaps):
    if hashlib.sha256(original.encode()).hexdigest() != PINNED_DRT_SHA256:
        raise ValueError("pinned detailed-routing script changed")
    if type(seed) is not int or seed < 0 or type(swaps) is not int or not 0 <= swaps <= 10:
        raise ValueError("invalid routing search parameters")
    return original.replace("lappend drt_args -or_seed 42",
                            f"lappend drt_args -or_seed {seed}\nlappend drt_args -or_k {swaps}")


def main():
    from librelane.steps.openroad import DetailedRouting
    from librelane.common import get_script_dir
    root = Path("/closure")
    manifest = json.loads((root / "manifest.json").read_text())
    original = (Path(get_script_dir()) / "openroad/drt.tcl").read_text()
    settings = manifest["routing_search"]
    script = root / "route-search.tcl"
    transform = antenna_script if manifest.get("antenna_checkpoint") else search_script
    script.write_text(transform(original, settings["seed"], settings["swaps_per_net"]))
    (root / "route-search-sha256.json").write_text(json.dumps({
        "original": PINNED_DRT_SHA256,
        "executed": hashlib.sha256(script.read_bytes()).hexdigest(), **settings}, indent=2) + "\n")
    DetailedRouting.get_script_path = lambda self: str(script)
    runpy.run_module("librelane", run_name="__main__")


def antenna_script(original, seed, swaps):
    """Enter the pinned post-DRT repair loop directly on an existing routed ODB."""
    script = search_script(original, seed, swaps)
    initial = 'drt_run $i {*}$drt_args\n\nincr i'
    if script.count(initial) != 1:
        raise ValueError('expected exactly one initial detailed-route call')
    script = script.replace(initial, 'log_cmd check_antennas\n'
        'write_db $::env(STEP_DIR)/antenna-input.odb\n\nincr i')
    # Diodes are newly created instances; reconnect their power pins before DRT.
    repair = 'set diodes_inserted [log_cmd repair_antennas {*}$arg_list]'
    if script.count(repair) != 1:
        raise ValueError('expected exactly one antenna repair loop')
    return script.replace(repair, repair +
        '\n        source $::env(SCRIPTS_DIR)/openroad/common/set_power_nets.tcl'
        '\n        set_global_connections')


if __name__ == "__main__":
    main()
