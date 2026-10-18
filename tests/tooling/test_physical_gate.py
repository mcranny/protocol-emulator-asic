import pytest

from tools import check_physical as gate


def test_complete_evidence(make_physical_evidence):
    assert gate.check(*make_physical_evidence())["passing_tests"]["gatelevel"] == 3


@pytest.mark.parametrize(
    "limit",
    [None, 2.5074, "1.5", True, float("nan")],
    ids=["limit=None", "limit=2.5074", "limit=1.5", "limit=True", "limit=nan"],
)
def test_zero_violations_cannot_hide_changed_transition_limit(
    make_physical_evidence, tmp_path, limit
):
    metrics, config, pre, gl = make_physical_evidence()
    if limit is None:
        del config["MAX_TRANSITION_CONSTRAINT"]
    else:
        config["MAX_TRANSITION_CONSTRAINT"] = limit
    with pytest.raises(ValueError, match="transition target"):
        gate.check(metrics, config, pre, gl)


@pytest.mark.parametrize(
    "failure",
    ["missing", "negative", "sentinel", "corner", "drc", "xml", "skip", "area"],
    ids=[
        "failure=missing",
        "failure=negative",
        "failure=sentinel",
        "failure=corner",
        "failure=drc",
        "failure=xml",
        "failure=skip",
        "failure=area",
    ],
)
def test_incomplete_or_failed_evidence(make_physical_evidence, tmp_path, failure):
    metrics, config, pre, gl = make_physical_evidence()
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
        gl.write_text("<testsuite><testcase><failure/></testcase></testsuite>")
    elif failure == "skip":
        gl.write_text("<testsuite><testcase><skipped/></testcase></testsuite>")
    elif failure == "area":
        metrics["design__instance__area__stdcell"] = 900001
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)


@pytest.mark.parametrize(
    "rule", gate.ELECTRICAL_RULES, ids=["rule=slew", "rule=fanout", "rule=cap"]
)
@pytest.mark.parametrize(
    "corner",
    (None, *gate.CORNERS),
    ids=[
        "corner=None",
        "corner=nom_fast_1p32V_m40C",
        "corner=nom_slow_1p08V_125C",
        "corner=nom_typ_1p20V_25C",
    ],
)
@pytest.mark.parametrize(
    "bad",
    [None, 1, -1, True, "0", float("nan"), float("inf")],
    ids=["bad=None", "bad=1", "bad=-1", "bad=True", "bad=0", "bad=nan", "bad=inf"],
)
def test_electrical_metrics_fail_closed(
    make_physical_evidence, tmp_path, rule, corner, bad
):
    metrics, config, pre, gl = make_physical_evidence()
    key = f"design__max_{rule}_violation__count"
    if corner is not None:
        key += f"__corner:{corner}"
    if bad is None:
        del metrics[key]
    else:
        metrics[key] = bad
    with pytest.raises(ValueError, match="metric|electrical"):
        gate.check(metrics, config, pre, gl)


@pytest.mark.parametrize(
    "case",
    [
        "footprint",
        "period",
        "r2r_count",
        "missing_r2r",
        "too_few_prechecks",
        "wrong_suites",
        "duplicate_suite",
    ],
    ids=[
        "case=footprint",
        "case=period",
        "case=r2r_count",
        "case=missing_r2r",
        "case=too_few_prechecks",
        "case=wrong_suites",
        "case=duplicate_suite",
    ],
)
def test_release_requirements_are_not_weakened(make_physical_evidence, tmp_path, case):
    metrics, config, pre, gl = make_physical_evidence()
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
        gl.write_text(
            gl.read_text().replace(
                "</testsuite>",
                '<testcase name="test_uart_firmware_acceptance"/></testsuite>',
            )
        )
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)


def test_same_revision_required():
    source = "a" * 40
    rtl = {"schema": 1, "kind": "rtl", "source_commit": source, "files": {"x": {}}}
    gl = dict(rtl, kind="gatelevel")
    gate.check_provenance(source, {"commit": source}, rtl, gl)
    for bad in (
        dict(gl, source_commit="b" * 40),
        dict(gl, files={}),
        dict(gl, kind="rtl"),
        dict(gl, schema=0),
    ):
        with pytest.raises(ValueError):
            gate.check_provenance(source, {"commit": source}, rtl, bad)
    with pytest.raises(ValueError, match="submission"):
        gate.check_provenance(source, {"commit": "b" * 40}, rtl, gl)


def test_reject_substituted_artifact(tmp_path):
    path = tmp_path / "results.xml"
    path.write_text("<testsuite/>")
    manifest = {
        "files": {
            "test/results.xml": {
                "sha256": gate.hashlib.sha256(path.read_bytes()).hexdigest(),
                "bytes": path.stat().st_size,
            }
        }
    }
    gate.verify_checksum(manifest, "test/results.xml", path)
    path.write_text("<testsuite><testcase/></testsuite>")
    with pytest.raises(ValueError, match="checksum"):
        gate.verify_checksum(manifest, "test/results.xml", path)


@pytest.mark.parametrize(
    "key",
    [
        "antenna__violating__nets",
        "antenna__violating__pins",
        "route__antenna_violation__count",
        *[
            f"timing__unannotated_net_filtered__count__corner:{corner}"
            for corner in gate.CORNERS
        ],
    ],
    ids=[
        "key=antenna__violating__nets",
        "key=antenna__violating__pins",
        "key=route__antenna_violation__count",
        "key=timing__unannotated_net_filtered__count__corner:nom_fast_1p32V_m40C",
        "key=timing__unannotated_net_filtered__count__corner:nom_slow_1p08V_125C",
        "key=timing__unannotated_net_filtered__count__corner:nom_typ_1p20V_25C",
    ],
)
@pytest.mark.parametrize(
    "missing", [False, True], ids=["missing=False", "missing=True"]
)
def test_antenna_and_parasitic_evidence(make_physical_evidence, tmp_path, key, missing):
    metrics, config, pre, gl = make_physical_evidence()
    if missing:
        del metrics[key]
    else:
        metrics[key] = 1
    with pytest.raises(ValueError):
        gate.check(metrics, config, pre, gl)
