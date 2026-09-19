import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("physical_gate", Path(__file__).parents[1] / "tools/check_physical.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def evidence(tmp_path):
    metrics = {"design__instance_unmapped__count": 0, "route__drc_errors": 0,
               "magic__drc_error__count": 0, "design__lvs_error__count": 0,
               "antenna__violating__nets": 0, "antenna__violating__pins": 0,
               "route__antenna_violation__count": 0,
               "design__instance__area__stdcell": 300000,
               "design__core__area": 900000,
               "design__instance__utilization__stdcell": 1 / 3}
    for corner in gate.CORNERS:
        metrics[f"timing__unannotated_net_filtered__count__corner:{corner}"] = 0
        for kind in ("setup", "hold"):
            metrics[f"timing__{kind}__ws__corner:{corner}"] = 1.0
            metrics[f"timing__{kind}_r2r__ws__corner:{corner}"] = 2.0
            metrics[f"timing__{kind}_vio__count__corner:{corner}"] = 0
            metrics[f"timing__{kind}_r2r_vio__count__corner:{corner}"] = 0
    for rule in gate.ELECTRICAL_RULES:
        key = f"design__max_{rule}_violation__count"
        metrics[key] = 0
        for corner in gate.CORNERS:
            metrics[f"{key}__corner:{corner}"] = 0
    config = {"CLOCK_PERIOD": 40, "STA_CORNERS": list(gate.CORNERS),
              "DIE_AREA": gate.DIE_AREA.copy()}
    pre = tmp_path / "precheck.xml"
    pre.write_text("<testsuite>" + "<testcase/>" * 9 + "</testsuite>")
    gl = tmp_path / "gatelevel.xml"
    gl.write_text("<testsuite>" + "".join(f'<testcase name="{name}"/>'
                  for name in sorted(gate.PIN_SUITES)) + "</testsuite>")
    return metrics, config, pre, gl


def test_complete_evidence(tmp_path):
    assert gate.check(*evidence(tmp_path))["passing_tests"]["gatelevel"] == 3


@pytest.mark.parametrize("failure", ["missing", "negative", "sentinel", "corner", "drc", "xml", "skip", "area"])
def test_incomplete_or_failed_evidence(tmp_path, failure):
    metrics, config, pre, gl = evidence(tmp_path)
    key = f"timing__setup__ws__corner:{gate.CORNERS[0]}"
    if failure == "missing":
        del metrics[key]
    elif failure == "negative":
        metrics[key] = -0.01
    elif failure == "sentinel":
        metrics[key] = 1.7976931348623157e308
    elif failure == "corner":
        config["STA_CORNERS"].pop()
    elif failure == "drc":
        metrics["magic__drc_error__count"] = 1
    elif failure == "xml":
        gl.write_text('<testsuite><testcase><failure/></testcase></testsuite>')
    elif failure == "skip":
        gl.write_text('<testsuite><testcase><skipped/></testcase></testsuite>')
    elif failure == "area":
        metrics["design__instance__area__stdcell"] = 900001
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)


@pytest.mark.parametrize("rule", gate.ELECTRICAL_RULES)
@pytest.mark.parametrize("corner", (None, *gate.CORNERS))
@pytest.mark.parametrize("bad", [None, 1, -1, True, "0", float("nan"), float("inf")])
def test_electrical_metrics_fail_closed(tmp_path, rule, corner, bad):
    metrics, config, pre, gl = evidence(tmp_path)
    key = f"design__max_{rule}_violation__count"
    if corner is not None:
        key += f"__corner:{corner}"
    if bad is None:
        del metrics[key]
    else:
        metrics[key] = bad
    with pytest.raises(ValueError, match="metric|electrical"):
        gate.check(metrics, config, pre, gl)


@pytest.mark.parametrize("case", ["footprint", "period", "r2r_count", "missing_r2r",
                                 "too_few_prechecks", "wrong_suites", "duplicate_suite"])
def test_release_contract_is_not_weakened(tmp_path, case):
    metrics, config, pre, gl = evidence(tmp_path)
    if case == "footprint":
        config["DIE_AREA"][2] += 100
    elif case == "period":
        config["CLOCK_PERIOD"] = 80
    elif case == "r2r_count":
        metrics[f"timing__hold_r2r_vio__count__corner:{gate.CORNERS[0]}"] = 1
    elif case == "missing_r2r":
        del metrics[f"timing__hold_r2r_vio__count__corner:{gate.CORNERS[0]}"]
    elif case == "too_few_prechecks":
        pre.write_text("<testsuite><testcase/></testsuite>")
    elif case == "wrong_suites":
        gl.write_text("<testsuite><testcase/><testcase/><testcase/></testsuite>")
    else:
        gl.write_text(gl.read_text().replace("</testsuite>",
                      '<testcase name="uart_firmware_acceptance"/></testsuite>'))
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)


def test_same_revision_required():
    source = "a" * 40
    rtl = {"schema": 1, "kind": "rtl", "source_commit": source, "files": {"x": {}}}
    gl = dict(rtl, kind="gatelevel")
    gate.check_provenance(source, {"commit": source}, rtl, gl)
    for bad in (dict(gl, source_commit="b" * 40), dict(gl, files={}),
                dict(gl, kind="rtl"), dict(gl, schema=0)):
        with pytest.raises(ValueError):
            gate.check_provenance(source, {"commit": source}, rtl, bad)
    with pytest.raises(ValueError, match="submission"):
        gate.check_provenance(source, {"commit": "b" * 40}, rtl, gl)


def test_reject_substituted_artifact(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text("<testsuite/>")
    manifest = {"files": {"test/results.xml": {
        "sha256": gate.hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size}}}
    gate.verify_checksum(manifest, "test/results.xml", path)
    path.write_text("<testsuite><testcase/></testsuite>")
    with pytest.raises(ValueError, match="checksum"):
        gate.verify_checksum(manifest, "test/results.xml", path)


@pytest.mark.parametrize("key", ["antenna__violating__nets", "antenna__violating__pins",
    "route__antenna_violation__count", *[
        f"timing__unannotated_net_filtered__count__corner:{corner}" for corner in gate.CORNERS]])
@pytest.mark.parametrize("missing", [False, True])
def test_antenna_and_parasitic_evidence(tmp_path, key, missing):
    metrics, config, pre, gl = evidence(tmp_path)
    if missing:
        del metrics[key]
    else:
        metrics[key] = 1
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)
