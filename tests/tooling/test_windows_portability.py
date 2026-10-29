"""Windows can run the Python tests without GNU make or Developer Mode."""

import os
from pathlib import Path

import pytest

from tests.support.symlink import symlink_or_skip
from tools import make_entry


def test_symlink_or_skip_skips_when_os_refuses(tmp_path, monkeypatch):
    def refuse(*_args, **_kwargs):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(os, "symlink", refuse)
    with pytest.raises(pytest.skip.Exception, match="symlinks are unavailable"):
        symlink_or_skip(tmp_path / "target", tmp_path / "link")


def test_symlink_or_skip_creates_a_link_when_allowed(tmp_path):
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"
    symlink_or_skip(target, link)
    assert link.is_symlink()
    assert link.read_text() == "x"


def test_pytest_entry_matches_makefile():
    recipe = "python -m pytest -q --junitxml=build/python-results.xml"
    assert recipe in Path("Makefile").read_text()
    assert make_entry.pytest_command()[1:] == [
        "-m",
        "pytest",
        "-q",
        "--junitxml=build/python-results.xml",
    ]


def test_unknown_make_target_is_rejected(monkeypatch):
    monkeypatch.setattr(make_entry.subprocess, "call", lambda *_args, **_kwargs: 0)
    assert make_entry.main(["formal"]) == 2


def test_pytest_target_runs_without_make(monkeypatch):
    seen = {}

    def fake_call(args, cwd):
        seen["args"] = args
        seen["cwd"] = cwd
        return 0

    monkeypatch.setattr(make_entry.subprocess, "call", fake_call)
    assert make_entry.main(["test"]) == 0
    assert seen["args"][1:3] == ["-m", "pytest"]
    assert seen["cwd"] == make_entry.ROOT
