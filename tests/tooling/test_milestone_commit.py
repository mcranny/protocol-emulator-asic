"""tools/milestone_commit.py refuses bad commits and writes clean ones.

Each test builds a throwaway git repository with a deliberately wrong
identity, so a pass proves the script set the author itself rather than
inheriting it.
"""
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tools" / "milestone_commit.py"
PASS = f"{sys.executable} -c \"import sys; sys.exit(0)\""
FAIL = f"{sys.executable} -c \"import sys; sys.exit(3)\""


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()


def run(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, capture_output=True, text=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q", "-b", "main")
    # Wrong on purpose: the script must override these.
    git(tmp_path, "config", "user.name", "Wrong Person")
    git(tmp_path, "config", "user.email", "wrong@example.com")
    (tmp_path / "seed.txt").write_text("seed\n")
    git(tmp_path, "add", "seed.txt")
    git(tmp_path, "-c", "user.name=Seed", "-c", "user.email=seed@example.com",
        "commit", "-q", "-m", "seed")
    git(tmp_path, "switch", "-q", "-c", "feature")
    (tmp_path / "work.txt").write_text("work\n")
    return tmp_path


def head_record(repo: Path) -> tuple[str, str, str, str, str]:
    an, ae, cn, ce, body = git(
        repo, "log", "-1", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B"
    ).split("\x00", 4)
    return an, ae, cn, ce, body


def test_a_clean_commit_has_the_required_identity(repo: Path) -> None:
    result = run(repo, "-a", "-m", "Add work", "-b", "Detail paragraph.", "--check", PASS)
    assert result.returncode == 0, result.stderr
    an, ae, cn, ce, body = head_record(repo)
    assert (an, ae) == ("Matthew Cranny", "crannymatthew@gmail.com")
    assert (cn, ce) == ("Matthew Cranny", "crannymatthew@gmail.com")
    # git() strips trailing whitespace, so compare without the final newline.
    assert body == "Add work\n\nDetail paragraph."


def test_the_message_has_no_trailer_even_with_a_hostile_config(repo: Path) -> None:
    # Simulate a global setting that would add a trailer via porcelain.
    git(repo, "config", "trailer.co.key", "Co-authored-by")
    result = run(repo, "-a", "-m", "Add work", "--check", PASS)
    assert result.returncode == 0, result.stderr
    assert "Co-authored-by" not in head_record(repo)[4]


def test_a_failing_check_commits_nothing(repo: Path) -> None:
    before = git(repo, "rev-parse", "HEAD")
    result = run(repo, "-a", "-m", "Add work", "--check", FAIL)
    assert result.returncode == 1
    assert "check failed with exit 3" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == before


def test_checks_run_in_order_and_stop_at_the_first_failure(repo: Path) -> None:
    marker = repo / "ran.txt"
    touch = f"{sys.executable} -c \"open(r'{marker}', 'w').write('x')\""
    result = run(repo, "-a", "-m", "Add work", "--check", FAIL, "--check", touch)
    assert result.returncode == 1
    assert not marker.exists(), "the second check must not run after the first fails"


def test_refuses_to_commit_on_main(repo: Path) -> None:
    git(repo, "switch", "-q", "main")
    result = run(repo, "-a", "-m", "Add work", "--check", PASS)
    assert result.returncode == 1
    assert "refusing to commit on 'main'" in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "Co-authored-by: Someone <s@example.com>",
        "Written with help from Cursor",
        "Generated with an assistant",
        "Reviewed by Claude",
    ],
)
def test_forbidden_text_in_the_message_is_rejected(repo: Path, text: str) -> None:
    before = git(repo, "rev-parse", "HEAD")
    result = run(repo, "-a", "-m", "Add work", "-b", text, "--check", PASS)
    assert result.returncode == 1
    assert "forbidden text" in result.stderr
    assert git(repo, "rev-parse", "HEAD") == before


def test_a_multiline_subject_is_rejected(repo: Path) -> None:
    result = run(repo, "-a", "-m", "Line one\nLine two", "--check", PASS)
    assert result.returncode == 1
    assert "single line" in result.stderr


def test_an_overlong_subject_is_rejected(repo: Path) -> None:
    result = run(repo, "-a", "-m", "x" * 73, "--check", PASS)
    assert result.returncode == 1
    assert "72" in result.stderr


def test_nothing_staged_is_an_error(repo: Path) -> None:
    (repo / "work.txt").unlink()
    result = run(repo, "-m", "Add work", "--check", PASS)
    assert result.returncode == 1
    assert "nothing is staged" in result.stderr


def test_without_dash_a_only_staged_files_are_committed(repo: Path) -> None:
    (repo / "other.txt").write_text("other\n")
    git(repo, "add", "work.txt")
    result = run(repo, "-m", "Add work", "--check", PASS)
    assert result.returncode == 0, result.stderr
    committed = git(repo, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed == ["work.txt"]
    assert "other.txt" in git(repo, "status", "--porcelain")


def test_dry_run_runs_checks_but_writes_nothing(repo: Path) -> None:
    before = git(repo, "rev-parse", "HEAD")
    result = run(repo, "-a", "-m", "Add work", "--check", PASS, "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "dry run" in result.stdout
    assert git(repo, "rev-parse", "HEAD") == before


def test_the_commit_has_exactly_one_parent(repo: Path) -> None:
    result = run(repo, "-a", "-m", "Add work", "--check", PASS)
    assert result.returncode == 0, result.stderr
    parents = git(repo, "log", "-1", "--format=%P").split()
    assert len(parents) == 1
