import json
from pathlib import Path
import subprocess

import pytest

from tools import validate_local as runner


def repository(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    subprocess.run(['git', 'init', '-q', str(root)], check=True)
    (root / 'source.v').write_text('module example; endmodule\n')
    subprocess.run(['git', 'add', 'source.v'], cwd=root, check=True)
    subprocess.run(['git', '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                    'commit', '-qm', 'Fixture'], cwd=root, check=True)
    return root


def test_source_inventory_detects_untracked_edits_and_deletion(tmp_path):
    root = repository(tmp_path)
    before = runner.source_state(root)
    assert not before['source_dirty']
    (root / 'new.v').write_text('new source')
    assert 'new.v' in runner.source_state(root)['inputs']
    (root / 'source.v').write_text('changed source')
    after = runner.source_state(root)
    assert after['source_dirty'] and after['source_commit'] == before['source_commit']
    assert after['inputs']['source.v'] != before['inputs']['source.v']
    (root / 'source.v').unlink()
    assert 'source.v' not in runner.source_state(root)['inputs']


def test_runner_refuses_dirty_release_before_allocating_output(tmp_path, monkeypatch):
    root = repository(tmp_path)
    (root / 'source.v').write_text('changed source')
    monkeypatch.setattr(runner, 'ROOT', root)
    output = tmp_path / 'evidence'
    with pytest.raises(ValueError, match='clean source'):
        runner.validate(output, require_clean=True)
    assert not output.exists()


def test_failed_tool_preserves_snapshot_and_failure_manifest(tmp_path, monkeypatch):
    root = repository(tmp_path)
    monkeypatch.setattr(runner, 'ROOT', root)
    real_run = subprocess.run
    def fail_tool(command, **kwargs):
        if command[0] == 'git':
            return real_run(command, **kwargs)
        kwargs['stdout'].write('tool failed\n')
        return subprocess.CompletedProcess(command, 7)
    monkeypatch.setattr(subprocess, 'run', fail_tool)
    output = tmp_path / 'evidence'
    with pytest.raises(ValueError, match='exit code 7'):
        runner.validate(output, require_clean=True)
    manifest = json.loads((output / 'stage.json').read_text())
    assert manifest['exit_code'] == 1
    assert manifest['commands'][0]['exit_code'] == 7
    assert (output / 'source/source.v').read_text() == (root / 'source.v').read_text()
    assert manifest['artifacts']['version-python.log'] == runner.checksum(output / 'artifacts/version-python.log')
