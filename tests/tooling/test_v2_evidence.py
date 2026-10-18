import sys

import pytest

from tools import v2_evidence as evidence


@pytest.fixture
def make_stage(tmp_path, monkeypatch):
    def create():
        monkeypatch.setattr(
            evidence,
            "source_state",
            lambda: {
                "source_commit": "a" * 40,
                "source_dirty": False,
                "inputs": {"src.v": "b" * 64},
            },
        )
        gds = tmp_path / "layout.gds"
        gds.write_bytes(b"layout fixture")
        result = tmp_path / "results.xml"
        command = [
            sys.executable,
            "-c",
            'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("<testsuite/>")',
            str(result),
        ]
        return gds, result, command

    return create


def test_stage_records_actual_command_and_hashes(make_stage, tmp_path, monkeypatch):
    gds, result, command = make_stage()
    output = tmp_path / "stage"
    evidence.record(output, "precheck", {"precheck.xml": result}, command, gds=gds)
    manifest = evidence.read(output / "stage.json")
    assert manifest["exit_code"] == 0 and manifest["command"] == command
    assert manifest["gds_sha256"] == evidence.checksum(gds)
    evidence.verify_files(output / "artifacts", manifest["artifacts"])


def test_stage_rejects_stale_output_before_running_command(
    make_stage, tmp_path, monkeypatch
):
    gds, result, command = make_stage()
    result.write_text("old result")
    with pytest.raises(ValueError, match="new artifact"):
        evidence.record(
            tmp_path / "stage", "precheck", {"precheck.xml": result}, command, gds=gds
        )
    assert result.read_text() == "old result"


def test_stage_detects_physical_input_changed_by_command(
    make_stage, tmp_path, monkeypatch
):
    gds, result, command = make_stage()
    command[2] += '; Path(sys.argv[2]).write_bytes(b"changed")'
    command.append(str(gds))
    output = tmp_path / "stage"
    with pytest.raises(ValueError, match="input changed"):
        evidence.record(output, "precheck", {"precheck.xml": result}, command, gds=gds)
    assert evidence.read(output / "stage.json")["exit_code"] == 1


def test_stage_missing_artifact_cannot_pass(make_stage, tmp_path, monkeypatch):
    gds, result, _ = make_stage()
    output = tmp_path / "stage"
    with pytest.raises(OSError):
        evidence.record(
            output,
            "precheck",
            {"precheck.xml": result},
            [sys.executable, "-c", "pass"],
            gds=gds,
        )
    assert evidence.read(output / "stage.json")["exit_code"] == 1


def test_checkpoint_continuation_rejects_dirty_or_failed_stage(tmp_path):
    import json

    from tools.v2_evidence import continuation_inputs

    identity = dict(
        source_commit="a" * 40, source_dirty=False, inputs={"src/example.v": "abc"}
    )
    (tmp_path / "manifest.json").write_text(
        json.dumps(dict(identity, source_dirty=True))
    )
    (tmp_path / "route-status.json").write_text(
        json.dumps(dict(phase="finished", passed=True))
    )
    with pytest.raises(ValueError, match="clean functional source"):
        continuation_inputs(tmp_path, identity)
    (tmp_path / "manifest.json").write_text(json.dumps(identity))
    (tmp_path / "route-status.json").write_text(
        json.dumps(dict(phase="finished", passed=False))
    )
    with pytest.raises(ValueError, match="incomplete or failed"):
        continuation_inputs(tmp_path, identity)


def test_checkpoint_metrics_cannot_be_substituted_or_relabelled(tmp_path):
    import json

    from tools.check_v2 import checksum
    from tools.v2_evidence import continuation_inputs

    def write(name, data):
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
        return p

    final = "runs/signoff/final/"
    netlist = write(final + "nl/tt_um_mcranny_protocol_emulator_v2.nl.v", "netlist")
    raw = write(final + "metrics.json", {"route__drc_errors": 0})
    audit = write(
        "mapping-audit.json",
        dict(passed=True, unmapped_count=0, netlist_sha256=checksum(netlist)),
    )
    write(
        "metrics-provenance.json",
        dict(raw_metrics_sha256=checksum(raw), mapping_audit_sha256=checksum(audit)),
    )
    derived = write(
        "acceptance-metrics.json",
        dict(route__drc_errors=0, design__instance_unmapped__count=0),
    )
    prepared = write("state.json", {"checkpoint": "bound"})
    identity = dict(
        source_commit="a" * 40, source_dirty=False, inputs={"src/example.v": "abc"}
    )
    write(
        "manifest.json",
        dict(identity, prepared_sha256={"state.json": checksum(prepared)}),
    )
    write("route-status.json", dict(phase="finished", passed=True, issues=[]))
    write(
        "physical-artifacts.json",
        {str(p.relative_to(tmp_path)): checksum(p) for p in (netlist, raw)},
    )
    assert continuation_inputs(tmp_path, identity)[1] == derived
    derived.write_text(
        json.dumps(
            dict(
                route__drc_errors=0,
                design__instance_unmapped__count=0,
                magic__drc_error__count=0,
            )
        )
    )
    with pytest.raises(ValueError, match="metric derivation"):
        continuation_inputs(tmp_path, identity)
    prepared.write_text("changed")
    with pytest.raises(ValueError, match="checksum mismatch"):
        continuation_inputs(tmp_path, identity)
