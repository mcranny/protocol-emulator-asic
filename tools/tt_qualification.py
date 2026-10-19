"""Bind the unmodified TT action to an approved source and complete results.

Prepared inputs are recorded transformations of a clean source revision. They
are never presented as an unchanged checkout or as historical acceptance.
"""

import argparse
import json
import os
import re
import shutil
from pathlib import Path

from tools import check_physical, validate_local
from tools.validate_gatelevel import PIN_SUITES
from tools.validate_local import CORE_SUITES
from tools.local_flow import IMAGE, PDK_REV, TT_REV, verify_checkout

ROOT = Path(__file__).resolve().parents[1]
TOP = "tt_um_mcranny_protocol_emulator_v2"
BASELINE_RECORD = "evidence/test-migration-3d5210a/frozen.json"
PINS = {
    "action": "3412659307918422f3f0727917cf9b499aaca588",
    "support_tools": TT_REV,
    "librelane": "3.1.0.dev3",
    "image": IMAGE,
    "pdk": PDK_REV,
}
REFS = {"refs/heads/main", "refs/heads/v2-current"}
PROOFS = ("engine", "v2-engine", "v2-capture", "v2-integration", "v2-input-filter")
SUITES = {
    "v1-core.xml": {
        "test_differential_directed",
        "test_differential_random",
        "test_differential_uart",
    },
    "v1-pins.xml": check_physical.PIN_SUITES,
    "v2-core.xml": CORE_SUITES,
    "v2-pins.xml": PIN_SUITES,
    "capture.xml": {"test_v2_capture_differential"},
    "v1-reset.xml": {"test_v1_reset_and_state_loss"},
    "v2-reset.xml": {"test_v2_reset_and_state_loss"},
}


def read(path):
    return json.loads(Path(path).read_text())


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def dispatch(candidate, revision, ref):
    if (
        not re.fullmatch(r"[0-9a-f]{40}", candidate)
        or candidate != revision
        or ref not in REFS
    ):
        raise ValueError(
            "candidate must equal the full workflow SHA on main or v2-current"
        )


def approval(environment, policies):
    rules = [
        rule
        for rule in environment.get("protection_rules", [])
        if rule["type"] == "required_reviewers"
    ]
    if (
        environment.get("name") != "v2-physical"
        or environment.get("can_admins_bypass") is not False
        or len(rules) != 1
        or rules[0].get("prevent_self_review") is not False
    ):
        raise ValueError("physical approval settings differ")
    reviewers = rules[0].get("reviewers", [])
    if (
        len(reviewers) != 1
        or reviewers[0].get("type") != "User"
        or reviewers[0].get("reviewer", {}).get("id") != 163694578
    ):
        raise ValueError("mcranny must be the required physical reviewer")
    if environment.get("deployment_branch_policy") != {
        "protected_branches": False,
        "custom_branch_policies": True,
    }:
        raise ValueError("custom physical branch policy is required")
    branches = policies.get("branch_policies", [])
    if len(branches) != 2 or {(p["name"], p["type"]) for p in branches} != {
        ("main", "branch"),
        ("v2-current", "branch"),
    }:
        raise ValueError("physical branches must be exactly main and v2-current")


def guard(destination, candidate, revision, ref):
    dispatch(candidate, revision, ref)
    state = validate_local.source_state(ROOT)
    if state["source_commit"] != candidate or state["source_dirty"]:
        raise ValueError("candidate checkout must be clean and match the workflow SHA")
    approval(
        read(destination / "environment.json"), read(destination / "branches.json")
    )
    write(destination / "origin.json", dict(state, dependencies=PINS, workflow_ref=ref))


def prepare(destination):
    origin = read(destination / "origin.json")
    current = validate_local.source_state(ROOT)
    if any(current[k] != origin[k] for k in current):
        raise ValueError("source changed after candidate guard")
    prepared = v2_candidate.prepare(destination / "prepared")
    manifest = read(prepared / "candidate-inputs.json")
    if any(manifest[k] != origin[k] for k in current):
        raise ValueError("prepared candidate source differs")
    # This CI checkout is isolated. Save originals before the recorded overlay.
    for name in origin["inputs"]:
        target = destination / "source" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    for name in manifest["prepared"]:
        shutil.copy2(prepared / name, ROOT / name)
    write(
        destination / "preparation.json",
        dict(
            manifest,
            dependencies=PINS,
            execution_workspace_transformed=True,
            script_substitutions=[],
            checkpoint_ecos=[],
        ),
    )


def source_matches(stage, origin):
    if (
        stage.get("source_commit") != origin["source_commit"]
        or stage.get("source_dirty") is not False
        or stage.get("inputs") != origin["inputs"]
    ):
        raise ValueError("stage source identity differs")


def submission_identity(directory, source):
    identity, pdk = read(directory / "submission.json"), read(directory / "pdk.json")
    if identity.get("commit") != source:
        raise ValueError("TT submission revision differs")
    if (
        pdk.get("PDK") != "ihp-sg13cmos5l"
        or pdk.get("PDK_VERSION") != PDK_REV
        or pdk.get("PDK_SOURCE") != "IHP-Open-PDK"
        or pdk.get("FLOW_VERSION") != PINS["librelane"]
    ):
        raise ValueError("TT submission dependencies differ")


def resolved_settings(prepared, config):
    for key, value in prepared.items():
        if key not in ("meta", "//") and config.get(key) != value:
            raise ValueError(f"resolved physical setting differs: {key}")
    for key, value in prepared.get("meta", {}).items():
        if config.get("meta", {}).get(key) != value:
            raise ValueError(f"resolved flow metadata differs: {key}")
    if (
        config.get("DESIGN_NAME") != TOP
        or config.get("CLOCK_UNCERTAINTY_CONSTRAINT") != 0.25
    ):
        raise ValueError("V2 top or fixed clock uncertainty differs")


def seal(kind, destination, pdk_root, execution_path="github_actions"):
    origin = read(destination / "origin.json")
    submission = ROOT / "tt_submission"
    identity = read(submission / "commit_id.json")
    pdk = read(submission / "pdk.json")
    if identity.get("commit") != origin["source_commit"]:
        raise ValueError("TT submission revision differs")
    if (
        pdk.get("PDK") != "ihp-sg13cmos5l"
        or pdk.get("PDK_VERSION") != PDK_REV
        or pdk.get("FLOW_VERSION") != PINS["librelane"]
    ):
        raise ValueError("TT submission dependencies differ")
    verify_checkout(pdk_root, PDK_REV)
    files = {
        "submission.json": submission / "commit_id.json",
        "pdk.json": submission / "pdk.json",
    }
    netlist = submission / (TOP + ".v")
    gds = submission / (TOP + ".gds")
    if kind == "physical":
        verify_checkout(ROOT / "tt", TT_REV)
        preparation = read(destination / "preparation.json")
        source_matches(preparation, origin)
        check_v2.verify_files(ROOT, preparation["prepared"])
        config = read(ROOT / "runs/wokwi/resolved.json")
        resolved_settings(read(destination / "prepared/src/config.json"), config)
        mapping = read(destination / "mapping.json")
        if mapping.get("passed") is not True or mapping.get(
            "netlist_sha256"
        ) != check_v2.checksum(netlist):
            raise ValueError("independent mapped-cell audit differs")
        files.update(
            {
                "metrics.json": ROOT / "runs/wokwi/final/metrics.json",
                "config.json": ROOT / "runs/wokwi/resolved.json",
                "mapping.json": destination / "mapping.json",
                "prepared-config.json": destination / "prepared/src/config.json",
                "preparation.json": destination / "preparation.json",
                "netlist.v": netlist,
                "layout.gds": gds,
            }
        )
    elif kind == "precheck":
        verify_checkout(ROOT / "tt", TT_REV)
        files["precheck.xml"] = ROOT / "tt/precheck/reports/results.xml"
        check_v2.passing_xml(
            files["precheck.xml"], required_names=check_v2.PRECHECK_SUITES
        )
    elif kind == "gatelevel":
        files.update(
            {
                "gatelevel.xml": ROOT / "test/tt/output/gatelevel.xml",
                "commands.json": ROOT / "test/tt/output/commands.json",
                "reset-gatelevel.json": ROOT / "test/tt/output/reset/audit.json",
                "v2-reset-gatelevel.xml": ROOT / "test/tt/output/reset/v2.xml",
            }
        )
        check_v2.passing_xml(files["gatelevel.xml"], required_names=check_v2.PIN_SUITES)
        if read(files["commands.json"]).get("netlist_sha256") != check_v2.checksum(
            netlist
        ):
            raise ValueError("simulated netlist differs from TT submission")
        from tools.validate_reset import check_record

        check_v2.passing_xml(
            files["v2-reset-gatelevel.xml"],
            required_names={"test_v2_reset_and_state_loss"},
        )
        check_record(
            read(files["reset-gatelevel.json"]),
            (2,),
            origin,
            "gatelevel",
            check_v2.checksum(netlist),
        )
    else:
        raise ValueError("unknown qualification stage")
    artifacts = destination / "artifacts"
    artifacts.mkdir(exist_ok=False)
    for name, path in files.items():
        shutil.copy2(path, artifacts / name)
    write(
        destination / "stage.json",
        dict(
            origin,
            stage=kind,
            exit_code=0,
            execution_path=execution_path,
            netlist_sha256=check_v2.checksum(netlist),
            gds_sha256=check_v2.checksum(gds),
            artifacts={name: check_v2.checksum(artifacts / name) for name in files},
        ),
    )


def functional(directory, origin):
    stage = read(directory / "stage.json")
    source_matches(stage, origin)
    if (
        stage.get("exit_code") != 0
        or not stage.get("commands")
        or any(c.get("exit_code") != 0 for c in stage["commands"])
    ):
        raise ValueError("functional gate failed or missing")
    check_v2.verify_files(directory / "source", stage["inputs"])
    check_v2.verify_files(directory / "artifacts", stage["artifacts"])
    root = directory / "artifacts"
    count = check_v2.python_suites(root / "python.xml", root / "python-collection.txt")
    if count < 583:
        raise ValueError("baseline Python cases are missing")
    if BASELINE_RECORD not in stage["inputs"]:
        raise ValueError("frozen baseline inventory missing")
    frozen = read(directory / "source" / BASELINE_RECORD)
    expected = [
        row["new"].replace(
            "test_release_requirements_is_not_weakened",
            "test_release_requirements_are_not_weakened",
        )
        for row in frozen["pytest"]
    ]
    actual = {
        line.strip()
        for line in (root / "python-collection.txt").read_text().splitlines()
        if line.startswith("tests/") and "::" in line
    }
    if len(expected) != 583 or len(set(expected)) != 583 or not set(expected) <= actual:
        raise ValueError("a frozen baseline case is missing or replaced")
    for name, suites in SUITES.items():
        check_v2.passing_xml(root / name, required_names=suites)
    from tools.validate_reset import check_initialization, check_record

    for name in (
        "initialization.json",
        "reset-rtl.json",
        "v1-reset.xml",
        "v2-reset.xml",
    ):
        if name not in stage["artifacts"]:
            raise ValueError("required initialization/reset evidence missing")
    check_initialization(read(root / "initialization.json"), stage["inputs"])
    check_record(read(root / "reset-rtl.json"), (1, 2), stage, "rtl")
    for name in PROOFS:
        if (
            "Induction step proven: SUCCESS!"
            not in (root / f"formal-{name}.log").read_text()
        ):
            raise ValueError("induction success missing")
    mutations = read(root / "mutations.json")
    if (
        len(mutations) != 6
        or {m["mutation"] for m in mutations} != check_v2.MUTATIONS
        or any(m.get("detected") is not True for m in mutations)
    ):
        raise ValueError("mutation detection missing")
    required = {
        f"{tool}-{top}.log"
        for tool in ("lint", "synth")
        for top in ("tt_um_mcranny_protocol_emulator", TOP)
    }
    names = [c["log"] for c in stage["commands"]]
    if (
        len(names) != len(set(names))
        or not required <= set(names)
        or not required <= stage["artifacts"].keys()
    ):
        raise ValueError("both tops must pass lint and synthesis")
    return count


def accept(directory, candidate, execution_path="github_actions"):
    if execution_path not in {"github_actions", "local_tt_flow"}:
        raise ValueError("unknown physical execution path")
    stages = {
        name: read(directory / name / "stage.json")
        for name in ("physical", "precheck", "gatelevel")
    }
    origin = stages["physical"]
    if origin["source_commit"] != candidate or origin.get("dependencies") != PINS:
        raise ValueError("candidate or dependencies differ")
    for name, stage in stages.items():
        source_matches(stage, origin)
        if (
            stage.get("stage") != name
            or stage.get("exit_code") != 0
            or stage.get("dependencies") != PINS
            or stage.get("execution_path") != execution_path
            or any(stage.get(k) != origin[k] for k in ("gds_sha256", "netlist_sha256"))
        ):
            raise ValueError("physical stage identity differs")
        check_v2.verify_files(directory / name / "artifacts", stage["artifacts"])
    if not check_v2.REQUIRED_INPUTS <= origin["inputs"].keys():
        raise ValueError("candidate source inventory incomplete")
    for name, stage in stages.items():
        required = {"submission.json", "pdk.json"} | {
            "physical": {
                "prepared-config.json",
                "preparation.json",
                "metrics.json",
                "config.json",
                "mapping.json",
                "netlist.v",
                "layout.gds",
            },
            "precheck": {"precheck.xml"},
            "gatelevel": {
                "gatelevel.xml",
                "commands.json",
                "reset-gatelevel.json",
                "v2-reset-gatelevel.xml",
            },
        }[name]
        if not required <= stage["artifacts"].keys():
            raise ValueError("stage artifact inventory incomplete")
        submission_identity(directory / name / "artifacts", candidate)
    if (
        read(directory / "gatelevel/artifacts/commands.json").get("netlist_sha256")
        != origin["netlist_sha256"]
    ):
        raise ValueError("simulated netlist differs")
    from tools.validate_reset import check_record

    check_record(
        read(directory / "gatelevel/artifacts/reset-gatelevel.json"),
        (2,),
        origin,
        "gatelevel",
        origin["netlist_sha256"],
    )
    check_v2.passing_xml(
        directory / "gatelevel/artifacts/v2-reset-gatelevel.xml",
        required_names={"test_v2_reset_and_state_loss"},
    )
    count = functional(directory / "functional", origin)
    physical = directory / "physical/artifacts"
    for filename, key in (
        ("netlist.v", "netlist_sha256"),
        ("layout.gds", "gds_sha256"),
    ):
        if check_v2.checksum(physical / filename) != origin[key]:
            raise ValueError("physical artifact identity differs")
    config, metrics, mapping = (
        read(physical / name)
        for name in ("config.json", "metrics.json", "mapping.json")
    )
    if (
        config.get("DESIGN_NAME") != TOP
        or config.get("CLOCK_UNCERTAINTY_CONSTRAINT") != 0.25
    ):
        raise ValueError("V2 top or uncertainty differs")
    if (
        mapping.get("passed") is not True
        or mapping.get("netlist_sha256") != origin["netlist_sha256"]
    ):
        raise ValueError("mapped-cell evidence differs")
    # Retain raw metrics; add only an independently measured cell inventory.
    preparation = read(physical / "preparation.json")
    source_matches(preparation, origin)
    if (
        preparation.get("dependencies") != PINS
        or preparation.get("script_substitutions") != []
        or preparation.get("checkpoint_ecos") != []
    ):
        raise ValueError("stock-flow preparation provenance differs")
    resolved_settings(read(physical / "prepared-config.json"), config)
    key = "design__instance_unmapped__count"
    if key in metrics and metrics[key] != mapping["unmapped_count"]:
        raise ValueError("raw and independently measured mapping metrics disagree")
    metrics = dict(metrics, **{key: mapping["unmapped_count"]})
    result = check_physical.check(
        metrics,
        config,
        directory / "precheck/artifacts/precheck.xml",
        directory / "gatelevel/artifacts/gatelevel.xml",
        pin_suites=check_v2.PIN_SUITES,
        precheck_suites=check_v2.PRECHECK_SUITES,
    )
    return dict(
        result,
        source_commit=candidate,
        dependencies=PINS,
        python_tests=count,
        execution_path=execution_path,
        scope="stock TT qualification with reset audit; final release qualification pending",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=(
            "guard",
            "prepare",
            "functional",
            "physical",
            "precheck",
            "gatelevel",
            "accept",
        ),
    )
    parser.add_argument("--directory", type=Path, default=Path("build/tt-ci"))
    parser.add_argument(
        "--execution-path",
        choices=("github_actions", "local_tt_flow"),
        default="github_actions",
    )
    parser.add_argument("--candidate", default=os.environ.get("CANDIDATE_SHA", ""))
    parser.add_argument("--revision", default=os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF", ""))
    parser.add_argument("--origin", type=Path, default=Path("build/tt-ci/origin.json"))
    args = parser.parse_args()
    destination = args.directory.resolve()
    if args.operation == "guard":
        guard(destination, args.candidate, args.revision, args.ref)
    elif args.operation == "prepare":
        prepare(destination)
    elif args.operation == "functional":
        print(functional(destination, read(args.origin)))
    elif args.operation == "accept":
        write(
            destination / "qualification.json",
            accept(destination, args.candidate, args.execution_path),
        )
    else:
        seal(
            args.operation,
            destination,
            Path(os.environ["PDK_ROOT"]),
            args.execution_path,
        )


if __name__ == "__main__":
    main()
