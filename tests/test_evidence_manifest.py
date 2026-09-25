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


@pytest.mark.parametrize("dirty", [False, True])
def test_v2_capture_and_verification_artifacts_have_checksums(tmp_path, monkeypatch, dirty):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    def command(args):
        return {"exit_code": 0, "output": (" M src/v2_capture.v" if dirty else "")
                if args[:2] == ["git", "status"] else "a" * 40}
    monkeypatch.setattr(manifest, "command", command)
    names = ("test/output/v2-uart-fault.json", "test/output/v2-uart-fault.vcd",
             "build/formal-v2-capture.log", "build/formal-v2-engine.log",
             "build/v2-mutations/results.json", "test/Makefile.v2pins",
             "tests/test_v2_scenario.py")
    for name in names:
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
    result = manifest.collect("rtl")
    assert result["source_dirty"] is dirty
    for name in names:
        assert result["files"][name]["sha256"] == hashlib.sha256(name.encode()).hexdigest()


def test_unavailable_cleanliness_is_not_reported_as_clean(monkeypatch):
    monkeypatch.delenv("GITHUB_SHA", raising=False)
    monkeypatch.setattr(manifest, "command", lambda args: {"exit_code": 1}
                        if args[:2] == ["git", "status"] else {"exit_code": 0, "output": "a" * 40})
    with pytest.raises(ValueError, match="cleanliness"):
        manifest.collect("rtl")
