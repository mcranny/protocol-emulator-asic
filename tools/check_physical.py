"""Validate downloaded physical artifacts without substituting missing evidence."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

CORNERS = ("nom_fast_1p32V_m40C", "nom_slow_1p08V_125C", "nom_typ_1p20V_25C")
DIE_AREA = [0, 0, 1289.28, 710.64]
ELECTRICAL_RULES = ("slew", "fanout", "cap")
PIN_SUITES = {"host_protocol_and_safety", "uart_firmware_acceptance",
              "host_phase_and_interrupted_uart"}


def number(data, key):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"missing or invalid metric: {key}")
    return value


def passing_xml(path, required_names=None, minimum=1):
    root = ET.parse(path).getroot()
    cases = list(root.iter("testcase"))
    if len(cases) < minimum:
        raise ValueError(f"insufficient test cases: {path}")
    names = [case.get("name") for case in cases]
    if required_names and (set(names) != required_names or len(names) != len(required_names)):
        raise ValueError(f"missing, duplicate, or unexpected acceptance suites: {path}")
    if any(list(root.iter(tag)) for tag in ("failure", "error", "skipped")):
        raise ValueError(f"failed, errored, or skipped tests: {path}")
    for suite in root.iter("testsuite"):
        if any(int(suite.get(key, "0")) for key in ("failures", "errors", "skipped")):
            raise ValueError(f"nonpassing test suite: {path}")
    return len(cases)


def check(metrics, config, precheck, gatelevel):
    if config.get("CLOCK_PERIOD") != 40:
        raise ValueError("release target must be 40 ns")
    if config.get("MAX_TRANSITION_CONSTRAINT") != 1.5:
        raise ValueError("release transition target must be 1.5 ns")
    if config.get("DIE_AREA") != DIE_AREA:
        raise ValueError("release footprint must remain 6x4 tiles")
    if set(config.get("STA_CORNERS", [])) != set(CORNERS):
        raise ValueError("required timing corners missing or changed")
    for key in ("design__instance_unmapped__count", "route__drc_errors",
                "magic__drc_error__count", "design__lvs_error__count",
                "antenna__violating__nets", "antenna__violating__pins",
                "route__antenna_violation__count"):
        if number(metrics, key) != 0:
            raise ValueError(f"nonzero signoff errors: {key}")
    area = number(metrics, "design__instance__area__stdcell")
    core = number(metrics, "design__core__area")
    utilization = number(metrics, "design__instance__utilization__stdcell")
    if not 0 < area < core or not 0 < utilization < 1:
        raise ValueError("invalid area/utilization or no area headroom")
    electrical = {}
    for rule in ELECTRICAL_RULES:
        base = f"design__max_{rule}_violation__count"
        electrical[rule] = {}
        for corner in (None, *CORNERS):
            key = base if corner is None else f"{base}__corner:{corner}"
            if number(metrics, key) != 0:
                raise ValueError(f"nonzero electrical violations: {key}")
            electrical[rule][corner or "aggregate"] = 0
    timing = {}
    for corner in CORNERS:
        timing[corner] = {}
        if number(metrics, f"timing__unannotated_net_filtered__count__corner:{corner}") != 0:
            raise ValueError(f"unannotated signal parasitics at {corner}")
        for kind in ("setup", "hold"):
            slack = number(metrics, f"timing__{kind}__ws__corner:{corner}")
            r2r = number(metrics, f"timing__{kind}_r2r__ws__corner:{corner}")
            count = number(metrics, f"timing__{kind}_vio__count__corner:{corner}")
            r2r_count = number(metrics, f"timing__{kind}_r2r_vio__count__corner:{corner}")
            if min(slack, r2r) < 0 or max(slack, r2r) > 1e6 or count != 0 or r2r_count != 0:
                raise ValueError(f"invalid/violating {kind} timing at {corner}")
            timing[corner][kind + "_slack_ns"] = slack
            timing[corner][kind + "_r2r_slack_ns"] = r2r
    tests = {"precheck": passing_xml(precheck, minimum=9),
             "gatelevel": passing_xml(gatelevel, required_names=PIN_SUITES)}
    return dict(stdcell_area_um2=area, core_area_um2=core,
                utilization_fraction=utilization, timing=timing, electrical=electrical,
                passing_tests=tests)



def check_provenance(source, submission, rtl, gatelevel):
    if len(source) != 40 or any(char not in "0123456789abcdef" for char in source):
        raise ValueError("invalid source commit")
    if submission.get("commit") != source:
        raise ValueError("submission source mismatch")
    for kind, manifest in (("rtl", rtl), ("gatelevel", gatelevel)):
        if manifest.get("schema") != 1 or manifest.get("kind") != kind:
            raise ValueError(f"invalid {kind} evidence manifest")
        if manifest.get("source_commit") != source:
            raise ValueError(f"{kind} source mismatch")
        if not isinstance(manifest.get("files"), dict) or not manifest["files"]:
            raise ValueError(f"missing {kind} artifact checksums")


def verify_checksum(manifest, name, path):
    recorded = manifest["files"].get(name)
    content = path.read_bytes()
    expected = {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
    if recorded != expected:
        raise ValueError(f"artifact checksum mismatch: {name}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--precheck", type=Path, required=True)
    parser.add_argument("--gatelevel", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    for name in ("submission", "rtl-manifest", "gatelevel-manifest",
                 "python-tests", "core-tests", "formal-log"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        submission = json.loads(args.submission.read_text())
        rtl = json.loads(args.rtl_manifest.read_text())
        gl = json.loads(args.gatelevel_manifest.read_text())
        check_provenance(args.source_commit, submission, rtl, gl)
        for name, path in (("build/python-results.xml", args.python_tests),
                           ("test/results_core.xml", args.core_tests),
                           ("build/formal-engine.log", args.formal_log)):
            verify_checksum(rtl, name, path)
        verify_checksum(gl, "test/results.xml", args.gatelevel)
        python_count = passing_xml(args.python_tests)
        core_count = passing_xml(args.core_tests, required_names={
            "differential_directed", "differential_random", "differential_uart"})
        if "Induction step proven: SUCCESS!" not in args.formal_log.read_text():
            raise ValueError("missing induction proof success")
        result = check(json.loads(args.metrics.read_text()), json.loads(args.config.read_text()),
                       args.precheck, args.gatelevel)
        result.update(source_commit=args.source_commit, python_tests=python_count,
                      core_suites=core_count, formal_induction="passed")
    except (ValueError, OSError, ET.ParseError) as exc:
        parser.exit(1, f"physical acceptance failed: {exc}\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
