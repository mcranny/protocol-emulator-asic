import sys

import pytest

from tools import v2_evidence as evidence


def stage(tmp_path, monkeypatch):
    monkeypatch.setattr(evidence, 'source_state', lambda: {
        'source_commit': 'a' * 40, 'source_dirty': False, 'inputs': {'src.v': 'b' * 64}})
    gds = tmp_path / 'layout.gds'
    gds.write_bytes(b'layout fixture')
    result = tmp_path / 'results.xml'
    command = [sys.executable, '-c',
               'from pathlib import Path; import sys; Path(sys.argv[1]).write_text("<testsuite/>")', str(result)]
    return gds, result, command


def test_stage_records_actual_command_and_hashes(tmp_path, monkeypatch):
    gds, result, command = stage(tmp_path, monkeypatch)
    output = tmp_path / 'stage'
    evidence.record(output, 'precheck', {'precheck.xml': result}, command, gds=gds)
    manifest = evidence.read(output / 'stage.json')
    assert manifest['exit_code'] == 0 and manifest['command'] == command
    assert manifest['gds_sha256'] == evidence.checksum(gds)
    evidence.verify_files(output / 'artifacts', manifest['artifacts'])


def test_stage_rejects_stale_output_before_running_command(tmp_path, monkeypatch):
    gds, result, command = stage(tmp_path, monkeypatch)
    result.write_text('old result')
    with pytest.raises(ValueError, match='new artifact'):
        evidence.record(tmp_path / 'stage', 'precheck', {'precheck.xml': result}, command, gds=gds)
    assert result.read_text() == 'old result'


def test_stage_detects_physical_input_changed_by_command(tmp_path, monkeypatch):
    gds, result, command = stage(tmp_path, monkeypatch)
    command[2] += '; Path(sys.argv[2]).write_bytes(b"changed")'
    command.append(str(gds))
    output = tmp_path / 'stage'
    with pytest.raises(ValueError, match='input changed'):
        evidence.record(output, 'precheck', {'precheck.xml': result}, command, gds=gds)
    assert evidence.read(output / 'stage.json')['exit_code'] == 1


def test_stage_missing_artifact_cannot_pass(tmp_path, monkeypatch):
    gds, result, _ = stage(tmp_path, monkeypatch)
    output = tmp_path / 'stage'
    with pytest.raises(OSError):
        evidence.record(output, 'precheck', {'precheck.xml': result}, [sys.executable, '-c', 'pass'], gds=gds)
    assert evidence.read(output / 'stage.json')['exit_code'] == 1
