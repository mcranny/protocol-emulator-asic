"""Offline precheck uses a strict dependency lock and distinguishes runtime tests."""

from types import SimpleNamespace

import pytest

from tools import v2_precheck as precheck


def test_exact_requirement_pins():
    assert precheck.requirements(
        "# lock\nPyYAML==6.0.1\n    # via precheck\ngdstk==0.9.52\n"
    ) == {"pyyaml": "6.0.1", "gdstk": "0.9.52"}


@pytest.mark.parametrize(
    "value",
    [
        "",
        "# only a comment",
        "gdstk>=0.9",
        "-r other.txt",
        "git+https://example.org",
        "PyYAML==6.0.1\npyyaml==6.0.1",
    ],
    ids=[
        "value=",
        "value=# only a comment",
        "value=gdstk>=0.9",
        "value=-r other.txt",
        "value=git+https://example.org",
        "value=PyYAML==6.0.1\npyyaml==6.0.1",
    ],
)
def test_rejects_unpinned_or_ambiguous_requirements(value):
    with pytest.raises(ValueError):
        precheck.requirements(value)


def test_runtime_fails_before_tool_execution_on_package_drift(tmp_path, monkeypatch):
    lock = tmp_path / "tt/precheck/requirements.txt"
    lock.parent.mkdir(parents=True)
    lock.write_text("gdstk==0.9.52\n")
    monkeypatch.setattr(
        precheck.importlib.metadata,
        "distributions",
        lambda **kwargs: [
            SimpleNamespace(metadata={"Name": "gdstk"}, version="0.9.53")
        ],
    )
    with pytest.raises(ValueError, match="inventory differs"):
        precheck.runtime_probe(tmp_path)


def test_package_tree_hash_detects_content_changes(tmp_path):
    path = tmp_path / "package.py"
    path.write_text("original")
    before = precheck.tree_hashes(tmp_path)
    path.write_text("changed")
    assert precheck.tree_hashes(tmp_path) != before
