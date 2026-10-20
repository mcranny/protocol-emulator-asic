"""Create a test symlink, or skip when Windows refuses."""

import os
from pathlib import Path

import pytest


def symlink_or_skip(target: Path, link: Path) -> None:
    """Point ``link`` at ``target``.

    Creating a symlink on Windows needs Developer Mode or an elevated
    process. Without it, ``os.symlink`` raises ``OSError`` and the calling
    test skips instead of erroring.
    """
    try:
        os.symlink(target, link)
    except OSError as exc:
        pytest.skip(f"symlinks are unavailable: {exc}")
