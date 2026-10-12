"""Record reviewed local TT evidence after a verified hosted physical timeout.

CI validates the report and original run identity. It does not download or
independently rerun the local physical checks, and leaves the timeout visible.
"""

import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from tools.tt_qualification import PINS, dispatch, read, write

COUNTS = {
    "v1_core": 3,
    "v1_pins": 3,
    "v2_core": 3,
    "v2_pins": 14,
    "capture": 1,
    "induction_proofs": 5,
    "detected_mutations": 6,
    "precheck": 9,
    "gatelevel": 14,
    "required_violations": 0,
    "rtl_reset_suites": 2,
    "gatelevel_reset_suites": 1,
    "initialization_elaborations": 7,
}


def timeout_job(original, jobs, candidate, check_run=None, annotations=()):
    """Bind a hosted timeout to one GDS job and its GitHub Actions annotation."""
    physical = [job for job in jobs.get("jobs", []) if job.get("name") == "gds"]
    if len(physical) != 1:
        raise ValueError("fallback requires exactly one hosted gds job")
    job = physical[0]
    if (
        type(job.get("id")) is not int
        or job["id"] <= 0
        or job.get("run_id") != original["id"]
        or job.get("head_sha") != candidate
        or job.get("status") != "completed"
        or job.get("run_attempt") != original.get("run_attempt")
    ):
        raise ValueError("hosted gds job identity differs")
    if job.get("conclusion") == "timed_out":
        return job
    check = check_run or {}
    if (
        job.get("conclusion") != "cancelled"
        or check.get("id") != job["id"]
        or check.get("head_sha") != candidate
        or check.get("name") != "gds"
        or check.get("status") != "completed"
        or check.get("conclusion") != "cancelled"
        or check.get("app", {}).get("slug") != "github-actions"
        or check.get("output", {}).get("annotations_count") != len(annotations)
        or not any(
            annotation.get("annotation_level") == "failure"
            and annotation.get("message")
            == "The job has exceeded the maximum execution time of 6h0m0s"
            for annotation in annotations
        )
    ):
        raise ValueError(
            "fallback requires a timed-out hosted gds job or its explicit "
            "GitHub Actions six-hour timeout annotation, not a design failure"
        )
    return job


def report(summary, original, jobs, candidate, check_run=None, annotations=()):
    if (
        summary.get("schema") != 1
        or summary.get("execution_path") != "local_tt_flow"
        or summary.get("source_commit") != candidate
        or summary.get("dependencies") != PINS
    ):
        raise ValueError("local report source or dependencies differ")
    if (
        type(summary.get("timed_out_run_id")) is not int
        or summary["timed_out_run_id"] <= 0
        or original.get("id") != summary["timed_out_run_id"]
        or original.get("head_sha") != candidate
        or original.get("status") != "completed"
        or original.get("path") != ".github/workflows/gds.yaml"
        or original.get("event") != "workflow_dispatch"
    ):
        raise ValueError("original hosted run identity differs")
    job = timeout_job(original, jobs, candidate, check_run, annotations)
    for key in ("bundle_sha256", "source_inventory_sha256", "prepared_inputs_sha256"):
        if not re.fullmatch(r"[0-9a-f]{64}", summary.get(key, "")):
            raise ValueError(f"missing local evidence checksum: {key}")
    location = urlparse(summary.get("bundle_location", ""))
    if (
        location.scheme != "https"
        or not location.hostname
        or location.username
        or location.password
    ):
        raise ValueError("local bundle requires a durable HTTPS artifact location")
    result = summary.get("result_summary", {})
    if (
        result.get("passed") is not True
        or type(result.get("pytest")) is not int
        or result["pytest"] < 583
        or any(
            type(result.get(k)) is not int or result[k] != n for k, n in COUNTS.items()
        )
    ):
        raise ValueError("local evidence summary is incomplete or failing")
    return dict(
        summary,
        original_run_url=original["html_url"],
        original_job_id=job["id"],
        original_job_conclusion=job["conclusion"],
        timeout_annotations=list(annotations),
        ci_reran_physical_checks=False,
        ci_performed=[
            "validated report fields",
            "verified hosted physical timeout identity",
        ],
        status="recorded local validation; physical checks were performed locally",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("summary", "original", "jobs", "check-run", "annotations", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    candidate = os.environ["CANDIDATE_SHA"]
    dispatch(candidate, os.environ["GITHUB_SHA"], os.environ["GITHUB_REF"])
    result = report(
        read(args.summary),
        read(args.original),
        read(args.jobs),
        candidate,
        read(args.check_run),
        read(args.annotations),
    )
    write(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
