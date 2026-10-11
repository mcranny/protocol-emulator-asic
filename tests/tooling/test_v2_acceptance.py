import json

import pytest

from tests.support.evidence import (
    build_physical_evidence,
    initialization_evidence,
    reset_evidence,
)
from tools import check_v2 as gate


def xml(names):
    return (
        "<testsuite>"
        + "".join(
            f'<testcase classname="tests.test_fixture" name="{n}"/>'
            for n in sorted(names)
        )
        + "</testsuite>"
    )


@pytest.fixture
def make_bundle(tmp_path):
    def create():
        metrics, config, _, _ = build_physical_evidence(tmp_path)
        config["DESIGN_NAME"] = "tt_um_mcranny_protocol_emulator_v2"
        root = tmp_path / "bundle"
        artifacts = root / "artifacts"
        artifacts.mkdir(parents=True)
        for name in gate.ARTIFACTS:
            (artifacts / name).write_text("fixture")
        suites = {
            "precheck.xml": gate.PRECHECK_SUITES,
            "gatelevel.xml": gate.PIN_SUITES,
            "v2-pins.xml": gate.PIN_SUITES,
            "v2-core.xml": gate.CORE_SUITES,
            "capture.xml": {"test_v2_capture_differential"},
            "python.xml": {f"test_{i}" for i in range(327)},
            "v1-core.xml": {
                "test_differential_directed",
                "test_differential_random",
                "test_differential_uart",
            },
            "v1-pins.xml": {
                "test_host_protocol_and_safety",
                "test_uart_firmware_acceptance",
                "test_host_phase_and_interrupted_uart",
            },
        }
        for name, names in suites.items():
            (artifacts / name).write_text(xml(names))
        for name in gate.ARTIFACTS:
            if name.startswith("formal-"):
                (artifacts / name).write_text("Induction step proven: SUCCESS!")
        for name, data in (
            ("metrics.json", metrics),
            ("config.json", config),
            (
                "mutations.json",
                [{"mutation": m, "detected": True} for m in gate.MUTATIONS],
            ),
            (
                "render-manifest.json",
                {
                    "source_sha256": gate.checksum(artifacts / "layout.gds"),
                    "svg_sha256": gate.checksum(artifacts / "layout.svg"),
                    "geometry_simplified": False,
                    "svg_inventory_matches_gds": True,
                    "layers_omitted": [],
                    "hierarchy_depth_limit": None,
                },
            ),
        ):
            (artifacts / name).write_text(json.dumps(data))
        (artifacts / "python-collection.txt").write_text(
            "\n".join(f"tests/test_fixture.py::test_{i}" for i in range(327))
        )
        names = gate.REQUIRED_INPUTS
        inputs = {}
        for name in names:
            path = root / "source" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(name)
            inputs[name] = gate.checksum(path)
        identity = dict(source_commit="a" * 40, source_dirty=False, inputs=inputs)
        for name, data in (
            ("initialization.json", initialization_evidence(inputs)),
            ("reset-rtl.json", reset_evidence(identity)),
            (
                "reset-gatelevel.json",
                reset_evidence(identity, (2,), gate.checksum(artifacts / "netlist.v")),
            ),
        ):
            (artifacts / name).write_text(json.dumps(data))
        for name, version in (
            ("v1-reset.xml", 1),
            ("v2-reset.xml", 2),
            ("v2-reset-gatelevel.xml", 2),
        ):
            (artifacts / name).write_text(
                xml({f"test_v{version}_reset_and_state_loss"})
            )
        hashes = {p.name: gate.checksum(p) for p in artifacts.iterdir()}
        data = {
            "schema": 2,
            "source_commit": "a" * 40,
            "inputs": inputs,
            "artifacts": hashes,
            "stages": {
                name: {
                    "source_commit": "a" * 40,
                    "source_dirty": False,
                    "exit_code": 0,
                    "inputs": inputs.copy(),
                }
                for name in gate.STAGES
            },
        }
        data["stages"]["gatelevel"]["netlist_sha256"] = hashes["netlist.v"]
        data["stages"]["precheck"]["gds_sha256"] = hashes["layout.gds"]
        (root / "evidence.json").write_text(json.dumps(data))
        return root, data

    return create


def test_complete_v2_bundle(make_bundle):
    root, _ = make_bundle()
    assert gate.accept(root)["passing_tests"] == {
        "precheck": 9,
        "gatelevel": len(gate.PIN_SUITES),
    }


@pytest.mark.parametrize(
    "corruption",
    [
        "dirty",
        "revision",
        "inputs",
        "netlist",
        "gds",
        "failed",
        "missing_stage",
        "missing_artifact",
        "changed_source",
        "v1_suites",
        "skipped",
        "render",
        "proof",
        "mutation",
        "python_missing",
        "python_duplicate",
        "python_uncollected",
    ],
    ids=[
        "corruption=dirty",
        "corruption=revision",
        "corruption=inputs",
        "corruption=netlist",
        "corruption=gds",
        "corruption=failed",
        "corruption=missing_stage",
        "corruption=missing_artifact",
        "corruption=changed_source",
        "corruption=v1_suites",
        "corruption=skipped",
        "corruption=render",
        "corruption=proof",
        "corruption=mutation",
        "corruption=python_missing",
        "corruption=python_duplicate",
        "corruption=python_uncollected",
    ],
)
def test_reject_incomplete_or_mixed_evidence(make_bundle, tmp_path, corruption):
    root, data = make_bundle()
    stage = data["stages"]["gatelevel"]
    if corruption == "dirty":
        stage["source_dirty"] = True
    elif corruption == "revision":
        stage["source_commit"] = "b" * 40
    elif corruption == "inputs":
        stage["inputs"] = {}
    elif corruption == "netlist":
        stage["netlist_sha256"] = "b" * 64
    elif corruption == "gds":
        data["stages"]["precheck"]["gds_sha256"] = "b" * 64
    elif corruption == "failed":
        stage["exit_code"] = 1
    elif corruption == "missing_stage":
        del data["stages"]["precheck"]
    elif corruption == "missing_artifact":
        (root / "artifacts/layout.gds").unlink()
    elif corruption == "changed_source":
        (root / "source/src/v2_engine.v").write_text("modified")
    else:
        name, text = {
            "v1_suites": ("gatelevel.xml", xml({"test_host_protocol_and_safety"})),
            "skipped": (
                "v2-pins.xml",
                xml(gate.PIN_SUITES).replace("</testsuite>", "<skipped/></testsuite>"),
            ),
            "render": ("render-manifest.json", "{}"),
            "proof": ("formal-v2-integration.log", "proof failed"),
            "mutation": ("mutations.json", "[]"),
            "python_missing": ("python.xml", xml({"test_0"})),
            "python_duplicate": (
                "python.xml",
                xml({"test_0"}).replace(
                    "</testsuite>",
                    '<testcase classname="tests.test_fixture" name="test_0"/></testsuite>',
                ),
            ),
            "python_uncollected": (
                "python-collection.txt",
                "tests/test_fixture.py::different",
            ),
        }[corruption]
        (root / "artifacts" / name).write_text(text)
        data["artifacts"][name] = gate.checksum(root / "artifacts" / name)
    (root / "evidence.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        gate.accept(root)
