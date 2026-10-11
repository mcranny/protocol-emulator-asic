"""Stock TT qualification rejects unapproved revisions and incomplete evidence."""

import copy

import pytest

from tests.support.evidence import build_physical_evidence
from tools import check_v2
from tools import tt_qualification as tt


@pytest.fixture
def approval_settings():
    return (
        {
            "name": "v2-physical",
            "can_admins_bypass": False,
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": False,
                    "reviewers": [{"type": "User", "reviewer": {"id": 163694578}}],
                }
            ],
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
        },
        {
            "branch_policies": [
                {"name": "main", "type": "branch"},
                {"name": "v2-current", "type": "branch"},
            ]
        },
    )


def test_owner_can_approve_without_administrator_bypass(approval_settings):
    tt.approval(*approval_settings)


@pytest.mark.parametrize(
    "change",
    ["bypass", "self_review", "reviewer", "missing_reviewer", "tag", "extra_branch"],
    ids=[
        "admin-bypass",
        "self-review-disabled",
        "wrong-reviewer",
        "no-reviewer",
        "tag-ref",
        "extra-branch",
    ],
)
def test_approval_rejects_incorrect_settings(approval_settings, change):
    environment, policies = copy.deepcopy(approval_settings)
    if change == "bypass":
        environment["can_admins_bypass"] = True
    elif change == "self_review":
        environment["protection_rules"][0]["prevent_self_review"] = True
    elif change == "reviewer":
        environment["protection_rules"][0]["reviewers"][0]["reviewer"]["id"] = 1
    elif change == "missing_reviewer":
        environment["protection_rules"] = []
    elif change == "tag":
        policies["branch_policies"][0]["type"] = "tag"
    else:
        policies["branch_policies"].append({"name": "*", "type": "branch"})
    with pytest.raises(ValueError):
        tt.approval(environment, policies)


@pytest.mark.parametrize(
    "candidate,revision,ref",
    [
        ("a" * 40, "a" * 40, "refs/heads/main"),
        ("b" * 40, "b" * 40, "refs/heads/v2-current"),
    ],
    ids=["main", "v2-current"],
)
def test_exact_candidate_on_eligible_branch(candidate, revision, ref):
    tt.dispatch(candidate, revision, ref)


@pytest.mark.parametrize(
    "candidate,revision,ref",
    [
        ("abc", "abc", "refs/heads/main"),
        ("a" * 40, "b" * 40, "refs/heads/main"),
        ("a" * 40, "a" * 40, "refs/tags/main"),
        ("a" * 40, "a" * 40, "refs/heads/experiment"),
    ],
    ids=["short-sha", "different-sha", "tag", "ineligible-branch"],
)
def test_dispatch_rejects_other_sources(candidate, revision, ref):
    with pytest.raises(ValueError):
        tt.dispatch(candidate, revision, ref)


def xml(names):
    return (
        "<testsuite>"
        + "".join(
            f'<testcase classname="tests.fixture" name="{n}"/>' for n in sorted(names)
        )
        + "</testsuite>"
    )


@pytest.fixture
def make_qualification(tmp_path):
    def create():
        root = tmp_path / "qualification"
        metrics, config, _, _ = build_physical_evidence(tmp_path)
        config.update(DESIGN_NAME=tt.TOP, CLOCK_UNCERTAINTY_CONSTRAINT=0.25)
        source = root / "functional/source"
        inputs = {}
        for name in check_v2.REQUIRED_INPUTS:
            path = source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            inputs[name] = check_v2.checksum(path)
        baseline_path = source / tt.BASELINE_RECORD
        tt.write(
            baseline_path,
            {"pytest": [{"new": f"tests/fixture.py::test_{i}"} for i in range(583)]},
        )
        inputs[tt.BASELINE_RECORD] = check_v2.checksum(baseline_path)
        origin = dict(
            source_commit="a" * 40,
            source_dirty=False,
            inputs=inputs,
            dependencies=tt.PINS,
        )
        netlist, gds = "mapped netlist", "layout bytes"
        import hashlib

        net_hash = hashlib.sha256(netlist.encode()).hexdigest()
        gds_hash = hashlib.sha256(gds.encode()).hexdigest()
        for kind in ("physical", "precheck", "gatelevel"):
            artifacts = root / kind / "artifacts"
            artifacts.mkdir(parents=True)
            tt.write(artifacts / "submission.json", {"commit": origin["source_commit"]})
            tt.write(
                artifacts / "pdk.json",
                {
                    "PDK": "ihp-sg13cmos5l",
                    "PDK_VERSION": tt.PDK_REV,
                    "PDK_SOURCE": "IHP-Open-PDK",
                    "FLOW_VERSION": tt.PINS["librelane"],
                },
            )
            if kind == "physical":
                tt.write(artifacts / "metrics.json", metrics)
                tt.write(artifacts / "config.json", config)
                tt.write(
                    artifacts / "prepared-config.json",
                    {"CLOCK_PERIOD": 40, "MAX_TRANSITION_CONSTRAINT": 1.5},
                )
                tt.write(
                    artifacts / "preparation.json",
                    dict(origin, script_substitutions=[], checkpoint_ecos=[]),
                )
                tt.write(
                    artifacts / "mapping.json",
                    {"passed": True, "netlist_sha256": net_hash, "unmapped_count": 0},
                )
                (artifacts / "netlist.v").write_text(netlist)
                (artifacts / "layout.gds").write_text(gds)
            elif kind == "precheck":
                (artifacts / "precheck.xml").write_text(xml(check_v2.PRECHECK_SUITES))
            else:
                (artifacts / "gatelevel.xml").write_text(xml(check_v2.PIN_SUITES))
                tt.write(artifacts / "commands.json", {"netlist_sha256": net_hash})
            tt.write(
                root / kind / "stage.json",
                dict(
                    origin,
                    stage=kind,
                    exit_code=0,
                    execution_path="github_actions",
                    netlist_sha256=net_hash,
                    gds_sha256=gds_hash,
                    artifacts={
                        p.name: check_v2.checksum(p) for p in artifacts.iterdir()
                    },
                ),
            )
        artifacts = root / "functional/artifacts"
        artifacts.mkdir()
        for name, names in tt.SUITES.items():
            (artifacts / name).write_text(xml(names))
        (artifacts / "python.xml").write_text(xml({f"test_{i}" for i in range(583)}))
        (artifacts / "python-collection.txt").write_text(
            "\n".join(f"tests/fixture.py::test_{i}" for i in range(583))
        )
        for name in tt.PROOFS:
            (artifacts / f"formal-{name}.log").write_text(
                "Induction step proven: SUCCESS!"
            )
        tt.write(
            artifacts / "mutations.json",
            [{"mutation": name, "detected": True} for name in check_v2.MUTATIONS],
        )
        commands = [
            {"log": f"{tool}-{top}.log", "exit_code": 0}
            for tool in ("lint", "synth")
            for top in ("tt_um_mcranny_protocol_emulator", tt.TOP)
        ]
        for command in commands:
            (artifacts / command["log"]).write_text("successful tool run")
        tt.write(
            root / "functional/stage.json",
            dict(
                origin,
                stage="functional",
                exit_code=0,
                commands=commands,
                artifacts={p.name: check_v2.checksum(p) for p in artifacts.iterdir()},
            ),
        )
        return root

    return create


def test_stock_results_require_all_existing_acceptance_checks(make_qualification):
    result = tt.accept(make_qualification(), "a" * 40)
    assert result["passing_tests"] == {"precheck": 9, "gatelevel": 14}
    assert result["python_tests"] == 583
    assert result["execution_path"] == "github_actions"


@pytest.mark.parametrize(
    "corruption",
    [
        "revision",
        "dirty",
        "dependency",
        "gds",
        "netlist",
        "source",
        "missing_stage",
        "unhashed",
        "empty",
        "skipped",
        "duplicate",
        "failed",
        "proof",
        "mutation",
        "uncertainty",
        "fanout",
        "pdk",
        "submission",
    ],
    ids=[
        "revision",
        "dirty",
        "dependency",
        "gds",
        "netlist",
        "source-edit",
        "missing-stage",
        "unhashed-result",
        "empty-result",
        "skipped-result",
        "duplicate-result",
        "failed-result",
        "bounded-only-proof",
        "undetected-mutation",
        "uncertainty",
        "fanout-violation",
        "wrong-pdk",
        "wrong-submission",
    ],
)
def test_corrupt_stock_evidence_is_rejected(make_qualification, corruption):
    root = make_qualification()
    stage_name, artifact = "gatelevel", None
    stage = tt.read(root / stage_name / "stage.json")
    if corruption == "revision":
        stage["source_commit"] = "b" * 40
    elif corruption == "dirty":
        stage["source_dirty"] = True
    elif corruption == "dependency":
        stage["dependencies"]["pdk"] = "b" * 40
    elif corruption in ("gds", "netlist"):
        stage[corruption + "_sha256"] = "b" * 64
    elif corruption == "source":
        (root / "functional/source/src/v2_engine.v").write_text("changed")
    elif corruption == "missing_stage":
        (root / "precheck/stage.json").unlink()
    elif corruption == "unhashed":
        del stage["artifacts"]["gatelevel.xml"]
    else:
        if corruption in ("proof", "mutation"):
            stage_name = "functional"
        elif corruption in ("uncertainty", "fanout", "pdk", "submission"):
            stage_name = "physical"
        stage = tt.read(root / stage_name / "stage.json")
        artifact = {
            "proof": "formal-v2-engine.log",
            "mutation": "mutations.json",
            "uncertainty": "config.json",
            "fanout": "metrics.json",
            "pdk": "pdk.json",
            "submission": "submission.json",
        }.get(corruption, "gatelevel.xml")
        path = root / stage_name / "artifacts" / artifact
        if corruption == "proof":
            path.write_text("bounded success only")
        elif corruption == "mutation":
            tt.write(path, [])
        elif corruption == "uncertainty":
            data = tt.read(path)
            data["CLOCK_UNCERTAINTY_CONSTRAINT"] = 0.1
            tt.write(path, data)
        elif corruption == "fanout":
            data = tt.read(path)
            data["design__max_fanout_violation__count"] = 1
            tt.write(path, data)
        elif corruption == "pdk":
            data = tt.read(path)
            data["PDK_VERSION"] = "b" * 40
            tt.write(path, data)
        elif corruption == "submission":
            tt.write(path, {"commit": "b" * 40})
        elif corruption == "empty":
            path.write_text("<testsuite/>")
        else:
            extra = {
                "skipped": "<skipped/>",
                "failed": "<failure/>",
                "duplicate": '<testcase name="test_v2_pin_transport_safety"/>',
            }[corruption]
            path.write_text(
                path.read_text().replace("</testsuite>", extra + "</testsuite>")
            )
        stage["artifacts"][artifact] = check_v2.checksum(path)
    tt.write(root / stage_name / "stage.json", stage)
    with pytest.raises((ValueError, FileNotFoundError)):
        tt.accept(root, "a" * 40)


def test_local_evidence_has_same_checks_and_explicit_execution_path(make_qualification):
    root = make_qualification()
    for name in ("physical", "precheck", "gatelevel"):
        path = root / name / "stage.json"
        stage = tt.read(path)
        stage["execution_path"] = "local_tt_flow"
        tt.write(path, stage)
    with pytest.raises(ValueError, match="stage identity"):
        tt.accept(root, "a" * 40)
    assert (
        tt.accept(root, "a" * 40, "local_tt_flow")["execution_path"] == "local_tt_flow"
    )


@pytest.fixture
def prepared_source(tmp_path, monkeypatch, approval_settings):
    import shutil

    root = tmp_path / "source"
    for name in (
        "info.yaml",
        "src/config.json",
        "physical/v2/info.yaml",
        "physical/v2/overrides.json",
        "src/host_spi.v",
        "src/v2_engine.v",
        "src/v2_capture.v",
        "src/v2_input_filter.v",
        "src/v2_project.v",
    ):
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(tt.ROOT / name, target)
    state = {
        "source_commit": "a" * 40,
        "source_dirty": False,
        "inputs": {
            str(p.relative_to(root)): check_v2.checksum(p)
            for p in root.rglob("*")
            if p.is_file()
        },
    }
    monkeypatch.setattr(tt, "ROOT", root)
    monkeypatch.setattr(tt.v2_candidate, "ROOT", root)
    monkeypatch.setattr(tt.validate_local, "source_state", lambda _: state.copy())
    monkeypatch.setattr(tt.v2_candidate, "source_state", lambda _: state.copy())
    destination = root / "build/tt-ci"
    tt.write(destination / "environment.json", approval_settings[0])
    tt.write(destination / "branches.json", approval_settings[1])
    return root, destination, state


def test_preparation_preserves_original_source_and_hashes_the_overlay(prepared_source):
    root, destination, state = prepared_source
    original_info = (root / "info.yaml").read_bytes()
    tt.guard(destination, "a" * 40, "a" * 40, "refs/heads/v2-current")
    tt.prepare(destination)
    preparation = tt.read(destination / "preparation.json")
    check_v2.verify_files(destination / "source", state["inputs"])
    check_v2.verify_files(root, preparation["prepared"])
    assert (destination / "source/info.yaml").read_bytes() == original_info
    assert (root / "info.yaml").read_bytes() == (
        root / "physical/v2/info.yaml"
    ).read_bytes()
    assert preparation["script_substitutions"] == preparation["checkpoint_ecos"] == []
    assert preparation["execution_workspace_transformed"] is True


def test_dirty_candidate_is_rejected_before_preparation(prepared_source):
    _, destination, state = prepared_source
    state["source_dirty"] = True
    with pytest.raises(ValueError, match="clean"):
        tt.guard(destination, "a" * 40, "a" * 40, "refs/heads/main")
    assert not (destination / "origin.json").exists()


def test_changed_source_after_guard_cannot_be_prepared(prepared_source):
    _, destination, state = prepared_source
    tt.guard(destination, "a" * 40, "a" * 40, "refs/heads/main")
    state["inputs"]["src/config.json"] = "b" * 64
    with pytest.raises(ValueError, match="changed"):
        tt.prepare(destination)
    assert not (destination / "prepared").exists()


def test_baseline_case_cannot_be_replaced_to_preserve_count(make_qualification):
    root = make_qualification()
    stage_path = root / "functional/stage.json"
    stage = tt.read(stage_path)
    for name in ("python.xml", "python-collection.txt"):
        path = root / "functional/artifacts" / name
        path.write_text(path.read_text().replace("test_0", "test_replacement"))
        stage["artifacts"][name] = check_v2.checksum(path)
    tt.write(stage_path, stage)
    with pytest.raises(ValueError, match="baseline case"):
        tt.accept(root, "a" * 40)
