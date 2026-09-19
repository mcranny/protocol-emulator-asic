import hashlib
import importlib.util
from pathlib import Path
import pytest

spec = importlib.util.spec_from_file_location("manifest", Path(__file__).parents[1] / "tools/collect_evidence.py")
manifest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manifest)


def test_artifact_checksums(tmp_path):
    path = tmp_path / "wave.fst"
    path.write_bytes(b"known waveform bytes")
    assert manifest.checksums([path, path, tmp_path / "missing"]) == {
        str(path): {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "bytes": 20}}


def test_manifest_is_bound_to_checkout(monkeypatch):
    monkeypatch.setattr(manifest, "command", lambda args: {"exit_code": 0, "output": "a" * 40})
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    with pytest.raises(ValueError, match="checkout"):
        manifest.collect("rtl")


def test_missing_tool_is_recorded():
    assert manifest.command(["nonexistent-protocol-evidence-tool"])["available"] is False
