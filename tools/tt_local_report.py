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


def report(summary, original, jobs, candidate):
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
    if not any(
        job.get("name") == "gds"
        and job.get("conclusion") == "timed_out"
        and job.get("run_id") == original["id"]
        for job in jobs.get("jobs", [])
    ):
        raise ValueError(
            "fallback requires a timed-out hosted gds job, not a design failure"
        )
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
        ci_reran_physical_checks=False,
        ci_performed=[
            "validated report fields",
            "verified hosted physical timeout identity",
        ],
        status="recorded local validation; physical checks were performed locally",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("summary", "original", "jobs", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    candidate = os.environ["CANDIDATE_SHA"]
    dispatch(candidate, os.environ["GITHUB_SHA"], os.environ["GITHUB_REF"])
    result = report(read(args.summary), read(args.original), read(args.jobs), candidate)
    write(args.output, result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
