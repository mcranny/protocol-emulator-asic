"""Synthetic physical artifacts for negative acceptance tests."""

from tools import check_physical as gate


def build_physical_evidence(tmp_path):
    metrics = {
        "design__instance_unmapped__count": 0,
        "route__drc_errors": 0,
        "magic__drc_error__count": 0,
        "design__lvs_error__count": 0,
        "antenna__violating__nets": 0,
        "antenna__violating__pins": 0,
        "route__antenna_violation__count": 0,
        "design__instance__area__stdcell": 300000,
        "design__core__area": 900000,
        "design__instance__utilization__stdcell": 1 / 3,
    }
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
    config = {
        "CLOCK_PERIOD": 40,
        "STA_CORNERS": list(gate.CORNERS),
        "DIE_AREA": gate.DIE_AREA.copy(),
        "MAX_TRANSITION_CONSTRAINT": 1.5,
    }
    pre = tmp_path / "precheck.xml"
    pre.write_text("<testsuite>" + "<testcase/>" * 9 + "</testsuite>")
    gl = tmp_path / "gatelevel.xml"
    gl.write_text(
        "<testsuite>"
        + "".join(f'<testcase name="{name}"/>' for name in sorted(gate.PIN_SUITES))
        + "</testsuite>"
    )
    return metrics, config, pre, gl
