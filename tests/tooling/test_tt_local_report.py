"""Local fallback reporting distinguishes a timeout from a physical failure."""

import subprocess

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
        run_attempt=1,
        head_sha="a" * 40,
        status="completed",
        event="workflow_dispatch",
        path=".github/workflows/gds.yaml",
        html_url="https://github.com/example/project/actions/runs/123",
    )
    jobs = {
        "jobs": [
            dict(
                id=456,
                name="gds",
                run_id=123,
                run_attempt=1,
                head_sha="a" * 40,
                status="completed",
                conclusion="timed_out",
            )
        ]
    }
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


@pytest.fixture
def annotated_timeout(local_report):
    summary, original, jobs = local_report
    jobs["jobs"][0]["conclusion"] = "cancelled"
    check = dict(
        id=456,
        name="gds",
        head_sha="a" * 40,
        status="completed",
        conclusion="cancelled",
        app={"slug": "github-actions"},
        output={"annotations_count": 2},
    )
    annotations = [
        dict(
            annotation_level="failure",
            message="The job has exceeded the maximum execution time of 6h0m0s",
        ),
        dict(annotation_level="failure", message="The operation was canceled."),
    ]
    return summary, original, jobs, check, annotations


def test_records_annotated_timeout_without_relabeling_cancellation(annotated_timeout):
    summary, original, jobs, check, annotations = annotated_timeout
    result = local.report(summary, original, jobs, "a" * 40, check, annotations)
    assert result["original_job_conclusion"] == "cancelled"
    assert result["original_job_id"] == 456
    assert result["timeout_annotations"] == annotations
    assert result["ci_reran_physical_checks"] is False


@pytest.mark.parametrize(
    "corruption",
    [
        "duplicate",
        "job-run",
        "job-sha",
        "job-status",
        "attempt",
        "check-id",
        "check-sha",
        "check-name",
        "check-status",
        "check-conclusion",
        "app",
        "truncated",
        "message",
        "level",
        "design-failure",
    ],
    ids=[
        "duplicate-gds",
        "different-run",
        "different-job-source",
        "unfinished-job",
        "different-attempt",
        "different-check-id",
        "different-check-source",
        "different-check-name",
        "unfinished-check",
        "successful-check",
        "untrusted-check-app",
        "missing-annotations",
        "ordinary-cancellation",
        "nonfailure-annotation",
        "annotated-design-failure",
    ],
)
def test_rejects_unbound_timeout_annotations(annotated_timeout, corruption):
    summary, original, jobs, check, annotations = annotated_timeout
    job = jobs["jobs"][0]
    if corruption == "duplicate":
        jobs["jobs"].append(job.copy())
    elif corruption == "job-run":
        job["run_id"] = 789
    elif corruption == "job-sha":
        job["head_sha"] = "b" * 40
    elif corruption == "job-status":
        job["status"] = "in_progress"
    elif corruption == "attempt":
        job["run_attempt"] = 2
    elif corruption == "check-id":
        check["id"] = 789
    elif corruption == "check-sha":
        check["head_sha"] = "b" * 40
    elif corruption == "check-name":
        check["name"] = "functional"
    elif corruption == "check-status":
        check["status"] = "in_progress"
    elif corruption == "check-conclusion":
        check["conclusion"] = "success"
    elif corruption == "app":
        check["app"]["slug"] = "untrusted"
    elif corruption == "truncated":
        annotations.pop()
    elif corruption == "message":
        annotations[0]["message"] = "The operation was canceled."
    elif corruption == "level":
        annotations[0]["annotation_level"] = "warning"
    else:
        job["conclusion"] = "failure"
    with pytest.raises(ValueError):
        local.report(summary, original, jobs, "a" * 40, check, annotations)


@pytest.fixture
def recovery_report(local_report):
    summary, original, jobs = local_report
    summary.update(
        schema=2, release_acceptance=False,
        result_summary=dict(local.RECOVERY_COUNTS, passed=True, pytest=631),
        recovery=dict(
            kind="checkpoint_rerun", source_commit="a" * 40,
            parent_result="failed", parent_failure="route__drc_errors=1",
            stock_scripts_unchanged=True, checkpoint_ecos=[],
            required_checks_passed=True, manifest_sha256="e" * 64,
            checkpoint_state_sha256="f" * 64, parent_failure_sha256="1" * 64,
            validation_summary_sha256="2" * 64, netlist_sha256="3" * 64,
            gds_sha256="4" * 64,
            config_changes={
                "OPENROAD_THREADS": {"before": None, "after": 8},
                "DRT_SAVE_SNAPSHOTS": {"before": False, "after": True},
                "DRT_SAVE_DRC_REPORT_ITERS": {"before": None, "after": 1},
                "RUN_KLAYOUT_DRC": {"before": False, "after": True},
                "KLAYOUT_DRC_THREADS": {"before": None, "after": 8},
            },
        ),
        supplemental_reset_audit=dict(
            source_commit="b" * 40, audit_sha256="5" * 64,
            netlist_sha256="3" * 64, gatelevel_reset_suites=1, passed=True),
    )
    return summary, original, jobs


def test_records_recovery_without_transferring_source_or_release_acceptance(recovery_report):
    result = local.report(*recovery_report, "a" * 40)
    assert result["source_commit"] == "a" * 40
    assert result["supplemental_reset_audit"]["source_commit"] == "b" * 40
    assert result["recovery"]["parent_result"] == "failed"
    assert result["release_acceptance"] is False
    assert result["ci_reran_physical_checks"] is False
    assert "final release acceptance remains pending" in result["status"]


@pytest.mark.parametrize("corruption", [
    "acceptance", "kind", "source", "parent", "failure", "script", "eco",
    "checks", "checkpoint-hash", "parent-hash", "configuration", "audit-source",
    "audit-hash", "audit-netlist", "audit-count", "audit-result", "mixed-counts",
    "physical-violations", "missing-pin-suite", "boolean-schema",
], ids=lambda value: value)
def test_rejects_incomplete_or_relabeled_recovery(recovery_report, corruption):
    summary, original, jobs = recovery_report
    recovery = summary["recovery"]
    audit = summary["supplemental_reset_audit"]
    if corruption == "acceptance": summary["release_acceptance"] = True
    elif corruption == "kind": recovery["kind"] = "clean_rtl"
    elif corruption == "source": recovery["source_commit"] = "b" * 40
    elif corruption == "parent": recovery["parent_result"] = "passed"
    elif corruption == "failure": del recovery["parent_failure"]
    elif corruption == "script": recovery["stock_scripts_unchanged"] = False
    elif corruption == "eco": recovery["checkpoint_ecos"] = ["remove diode"]
    elif corruption == "checks": recovery["required_checks_passed"] = False
    elif corruption == "checkpoint-hash": del recovery["checkpoint_state_sha256"]
    elif corruption == "parent-hash": recovery["parent_failure_sha256"] = "unknown"
    elif corruption == "configuration": recovery["config_changes"]["CLOCK_PERIOD"] = {"before": 40, "after": 80}
    elif corruption == "audit-source": audit["source_commit"] = "short"
    elif corruption == "audit-hash": del audit["audit_sha256"]
    elif corruption == "audit-netlist": audit["netlist_sha256"] = "f" * 64
    elif corruption == "audit-count": audit["gatelevel_reset_suites"] = True
    elif corruption == "audit-result": audit["passed"] = False
    elif corruption == "mixed-counts": summary["result_summary"]["rtl_reset_suites"] = 2
    elif corruption == "physical-violations": summary["result_summary"]["required_violations"] = 1
    elif corruption == "missing-pin-suite": del summary["result_summary"]["gatelevel"]
    else: summary["schema"] = True
    with pytest.raises(ValueError):
        local.report(summary, original, jobs, "a" * 40)


@pytest.fixture
def reporting_repo(tmp_path):
    def git(*args):
        return subprocess.check_output(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.test", *args],
            cwd=tmp_path, text=True, stderr=subprocess.DEVNULL).strip()
    git("init")
    git("commit", "--allow-empty", "-m", "Candidate")
    candidate = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "Reporter")
    reporter = git("rev-parse", "HEAD")
    return tmp_path, candidate, reporter


def test_reports_ancestor_candidate_from_exact_reporter_checkout(reporting_repo):
    root, candidate, reporter = reporting_repo
    local.reporting_revision(candidate, reporter, "refs/heads/v2-current", root)
    local.reporting_revision(reporter, reporter, "refs/heads/main", root)
    # Qualification retains the stronger same-revision requirement.
    with pytest.raises(ValueError):
        local.dispatch(candidate, reporter, "refs/heads/v2-current")


@pytest.mark.parametrize("corruption", ["unknown", "short", "checkout", "ref"],
                         ids=["unknown-candidate", "short-sha", "wrong-checkout", "ineligible-ref"])
def test_rejects_unbound_reporter_revision(reporting_repo, corruption):
    root, candidate, reporter = reporting_repo
    ref = "refs/heads/v2-current"
    if corruption == "unknown": candidate = "a" * 40
    elif corruption == "short": candidate = candidate[:7]
    elif corruption == "checkout": reporter = candidate
    else: ref = "refs/heads/other"
    with pytest.raises(ValueError):
        local.reporting_revision(candidate, reporter, ref, root)
