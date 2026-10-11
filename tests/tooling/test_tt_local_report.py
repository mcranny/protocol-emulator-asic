"""Local fallback reporting distinguishes a timeout from a physical failure."""

import pytest

from tools import tt_local_report as local


@pytest.fixture
def local_report():
    summary = dict(
        schema=1,
        execution_path="local_tt_flow",
        source_commit="a" * 40,
        dependencies=local.PINS.copy(),
        timed_out_run_id=123,
        bundle_sha256="b" * 64,
        source_inventory_sha256="c" * 64,
        prepared_inputs_sha256="d" * 64,
        bundle_location="https://example.test/evidence.tar.gz",
        result_summary=dict(local.COUNTS, passed=True, pytest=583),
    )
    original = dict(
        id=123,
        head_sha="a" * 40,
        status="completed",
        event="workflow_dispatch",
        path=".github/workflows/gds.yaml",
        html_url="https://github.com/example/project/actions/runs/123",
    )
    jobs = {"jobs": [{"name": "gds", "run_id": 123, "conclusion": "timed_out"}]}
    return summary, original, jobs


def test_recording_preserves_timeout_and_does_not_claim_ci_validation(local_report):
    result = local.report(*local_report, "a" * 40)
    assert result["execution_path"] == "local_tt_flow"
    assert result["ci_reran_physical_checks"] is False
    assert result["original_run_url"].endswith("/123")
    assert result["bundle_sha256"] == "b" * 64


@pytest.mark.parametrize(
    "corruption",
    [
        "failure",
        "cancelled",
        "different_job",
        "revision",
        "workflow",
        "digest",
        "location",
        "dependency",
        "violations",
        "missing_suite",
    ],
    ids=[
        "design-failure",
        "cancelled-job",
        "other-job",
        "other-revision",
        "other-workflow",
        "bad-digest",
        "missing-location",
        "changed-dependency",
        "nonzero-violations",
        "missing-suite",
    ],
)
def test_rejects_unqualified_fallback(local_report, corruption):
    summary, original, jobs = local_report
    if corruption in ("failure", "cancelled"):
        jobs["jobs"][0]["conclusion"] = corruption
    elif corruption == "different_job":
        jobs["jobs"][0]["name"] = "functional"
    elif corruption == "revision":
        original["head_sha"] = "b" * 40
    elif corruption == "workflow":
        original["path"] = ".github/workflows/other.yaml"
    elif corruption == "digest":
        summary["bundle_sha256"] = "partial"
    elif corruption == "location":
        summary["bundle_location"] = ""
    elif corruption == "dependency":
        summary["dependencies"]["librelane"] = "other"
    elif corruption == "violations":
        summary["result_summary"]["required_violations"] = 1
    else:
        del summary["result_summary"]["gatelevel"]
    with pytest.raises(ValueError):
        local.report(summary, original, jobs, "a" * 40)
