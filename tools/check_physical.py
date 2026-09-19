"""Validate downloaded physical artifacts without substituting missing evidence."""
import argparse
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

CORNERS = ("nom_fast_1p32V_m40C", "nom_slow_1p08V_125C", "nom_typ_1p20V_25C")


def number(data, key):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"missing or invalid metric: {key}")
    return value


def passing_xml(path):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if not cases:
        raise ValueError(f"no test cases: {path}")
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError(f"failed, errored, or skipped tests: {path}")
    for suite in root.iter("testsuite"):
        if any(int(suite.get(key, "0")) for key in ("failures", "errors", "skipped")):
            raise ValueError(f"nonpassing test suite: {path}")
    return len(cases)


def check(metrics, config, precheck, gatelevel):
    if config.get("CLOCK_PERIOD") != 40:
        raise ValueError("release target must be 40 ns")
    if set(config.get("STA_CORNERS", [])) != set(CORNERS):
        raise ValueError("required timing corners missing or changed")
    for key in ("design__instance_unmapped__count", "route__drc_errors",
                "magic__drc_error__count", "design__lvs_error__count"):
        if number(metrics, key) != 0:
            raise ValueError(f"nonzero signoff errors: {key}")
    area = number(metrics, "design__instance__area__stdcell")
    core = number(metrics, "design__core__area")
    utilization = number(metrics, "design__instance__utilization__stdcell")
    if not 0 < area < core or not 0 < utilization < 1:
        raise ValueError("invalid area/utilization or no area headroom")
    timing = {}
    for corner in CORNERS:
        timing[corner] = {}
        for kind in ("setup", "hold"):
            slack = number(metrics, f"timing__{kind}__ws__corner:{corner}")
            r2r = number(metrics, f"timing__{kind}_r2r__ws__corner:{corner}")
            count = number(metrics, f"timing__{kind}_vio__count__corner:{corner}")
            if min(slack, r2r) < 0 or max(slack, r2r) > 1e6 or count != 0:
                raise ValueError(f"invalid/violating {kind} timing at {corner}")
            timing[corner][kind + "_slack_ns"] = slack
            timing[corner][kind + "_r2r_slack_ns"] = r2r
    tests = {"precheck": passing_xml(precheck), "gatelevel": passing_xml(gatelevel)}
    if tests["gatelevel"] < 3:
        raise ValueError("expected all three pin-level acceptance suites")
    return dict(stdcell_area_um2=area, core_area_um2=core,
                utilization_fraction=utilization, timing=timing, passing_tests=tests)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--precheck", type=Path, required=True)
    parser.add_argument("--gatelevel", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = check(json.loads(args.metrics.read_text()), json.loads(args.config.read_text()),
                       args.precheck, args.gatelevel)
    except (ValueError, OSError, ET.ParseError) as exc:
        parser.exit(1, f"physical acceptance failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
