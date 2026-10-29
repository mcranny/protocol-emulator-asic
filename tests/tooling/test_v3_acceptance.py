import json
from pathlib import Path

import pytest

from tools.v3_acceptance import GATE_SKIPS, LAYOUT, MARGINS, main

GLITCH = next(iter(GATE_SKIPS))


def _junit(path: Path, cases: dict[str, str]) -> Path:
    body = {"pass": "", "fail": "<failure message='x'/>", "skip": "<skipped/>"}
    rows = "".join(f'<testcase name="{name}">{body[outcome]}</testcase>'
                   for name, outcome in cases.items())
    path.write_text(f"<testsuites><testsuite>{rows}</testsuite></testsuites>")
    return path


def _build(tmp_path: Path, *, evidence=None, metrics=None, precheck=None,
           gate=None):
    run = tmp_path / "run"
    for name, relative in LAYOUT.items():
        (run / relative).parent.mkdir(parents=True, exist_ok=True)
        (run / relative).write_text(name)
    (run / LAYOUT["metrics"]).write_text(json.dumps(
        {key: minimum + 0.5 for key, minimum in MARGINS.items()} | (metrics or {})))
    (run / "local-evidence.json").write_text(json.dumps(
        {"stage": "full", "exit_code": 0, "route_issues": [], "source_dirty": False,
         "source_commit": "abc"} | (evidence or {})))
    args = [
        "--run", str(run),
        "--precheck", str(_junit(tmp_path / "pre.xml", precheck or {"Pin check": "pass"})),
        "--gatelevel", str(_junit(tmp_path / "gl.xml",
                                  gate or {"test_a": "pass", GLITCH: "skip"})),
        "--output", str(tmp_path / "out" / "acceptance.json"),
    ]
    return args, tmp_path / "out" / "acceptance.json"


def test_accepts_a_build_that_meets_every_check(tmp_path: Path):
    args, out = _build(tmp_path)
    assert main(args) == 0
    verdict = json.loads(out.read_text())
    assert verdict["accepted"] and verdict["issues"] == []
    assert verdict["source_commit"] == "abc"
    assert set(verdict["sha256"]) == set(LAYOUT)


def test_the_watcher_reconfigure_test_is_an_allowed_gate_level_skip(tmp_path: Path):
    """It reads internal signals (events_r, e0_w0_en) a flat gate netlist drops."""
    name = "test_v3_reconfigure_drops_the_previous_watchers"
    assert name in GATE_SKIPS
    args, out = _build(tmp_path, gate={"test_a": "pass", name: "skip"})
    assert main(args) == 0
    assert json.loads(out.read_text())["issues"] == []


def test_accepts_every_allowed_gate_level_skip(tmp_path: Path):
    """Every name in GATE_SKIPS is an allowed skip, not just the first."""
    for name in GATE_SKIPS:
        args, out = _build(tmp_path, gate={"test_a": "pass", name: "skip"})
        assert main(args) == 0, f"{name} was not accepted as a gate-level skip"
        assert json.loads(out.read_text())["issues"] == []


@pytest.mark.parametrize("change, expected", [
    ({"evidence": {"source_dirty": True}}, "modified checkout"),
    ({"evidence": {"exit_code": 2}}, "flow exit code 2"),
    ({"evidence": {"stage": "route"}}, "not full"),
    ({"evidence": {"route_issues": ["route__drc_errors = 3"]}}, "route__drc_errors"),
    ({"metrics": {"timing__hold__ws__corner:nom_fast_1p32V_m40C": 0.09}}, "below 0.1"),
    ({"metrics": {"timing__setup_r2r__ws__corner:nom_slow_1p08V_125C": 1.9}}, "below 2.0"),
    ({"precheck": {"Pin check": "fail"}}, "precheck Pin check: fail"),
    ({"gate": {"test_a": "fail"}}, "gate-level test_a: fail"),
    ({"gate": {"test_a": "skip"}}, "gate-level test_a: skip"),
])
def test_rejects_each_missed_check(tmp_path: Path, change, expected):
    args, out = _build(tmp_path, **change)
    assert main(args) == 1
    verdict = json.loads(out.read_text())
    assert not verdict["accepted"]
    assert any(expected in issue for issue in verdict["issues"]), verdict["issues"]


def test_rejects_an_empty_precheck_report(tmp_path: Path):
    args, out = _build(tmp_path)
    _junit(tmp_path / "pre.xml", {})
    assert main(args) == 1
    assert "precheck report lists no checks" in json.loads(out.read_text())["issues"]
