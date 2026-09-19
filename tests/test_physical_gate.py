import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("physical_gate", Path(__file__).parents[1] / "tools/check_physical.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def evidence(tmp_path):
    metrics = {"design__instance_unmapped__count": 0, "route__drc_errors": 0,
               "magic__drc_error__count": 0, "design__lvs_error__count": 0,
               "design__instance__area__stdcell": 300000,
               "design__core__area": 900000,
               "design__instance__utilization__stdcell": 1 / 3}
    for corner in gate.CORNERS:
        for kind in ("setup", "hold"):
            metrics[f"timing__{kind}__ws__corner:{corner}"] = 1.0
            metrics[f"timing__{kind}_r2r__ws__corner:{corner}"] = 2.0
            metrics[f"timing__{kind}_vio__count__corner:{corner}"] = 0
    config = {"CLOCK_PERIOD": 40, "STA_CORNERS": list(gate.CORNERS)}
    xml = tmp_path / "results.xml"
    xml.write_text('<testsuite tests="3" failures="0"><testcase/><testcase/><testcase/></testsuite>')
    return metrics, config, xml, xml


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
