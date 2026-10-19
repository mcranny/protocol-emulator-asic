"""Commit a verified milestone with a clean author and no injected trailers.

This is the only supported way to commit in this repository. It exists
because the agent harness rewrites any shell command containing
``git commit`` to append ``--trailer "Co-authored-by: Cursor <...>"`` before
git runs. This script never invokes ``git commit``. It builds the commit with
plumbing (``write-tree``, ``commit-tree``, ``update-ref``), which has no
``--trailer`` option, so a rewrite would fail loudly instead of injecting.

Policy enforced here, not by convention:

* Commits land only on a non-default branch (never ``main`` or ``master``).
* Every configured check must pass first. A milestone is something that has
  been confirmed to work, so the default check is the full test suite.
* The author and committer are exactly ``Matthew Cranny
  <crannymatthew@gmail.com>``.
* The message is rejected if it mentions a co-author, an AI tool, or a
  generated-by line. After the commit is written it is read back and the
  reference is rolled back if any of those checks fail.

Usage::

    python tools/milestone_commit.py -m "Subject line" [-b "Body"] [-a]
        [--check "cmd"]... [--dry-run]

``-a`` stages every change first. Without it, only what is already staged is
committed. ``--check`` replaces the default check list; repeat it for several
commands. ``--dry-run`` runs the checks and the validation but writes nothing.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

AUTHOR_NAME = "Matthew Cranny"
AUTHOR_EMAIL = "crannymatthew@gmail.com"

DEFAULT_CHECKS = [f"{sys.executable} -m pytest tests -q"]

PROTECTED_BRANCHES = {"main", "master"}

#: Any match in the subject or body rejects the commit.
#:
#: "cursor" on its own is deliberately not here: the V3 architecture is the
#: time-cursor architecture and commit messages will say so. The agent is
#: matched by the forms that actually appear in injected trailers.
FORBIDDEN = re.compile(
    r"co-authored-by|cursoragent|cursor\.com|cursor agent|cursor ai|"
    r"claude|copilot|codex|chatgpt|openai|anthropic|"
    r"generated (with|by)|ai[- ]generated",
    re.IGNORECASE,
)


class MilestoneError(Exception):
    """The commit was refused. The message says why."""


def git(*args: str, input_text: str | None = None, env: dict[str, str] | None = None) -> str:
    """Run a git command and return stripped stdout; raise on failure."""
    result = subprocess.run(
        ["git", *args],
        input=input_text,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        raise MilestoneError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def current_branch() -> str:
    return git("rev-parse", "--abbrev-ref", "HEAD")


def require_feature_branch() -> str:
    branch = current_branch()
    if branch in PROTECTED_BRANCHES or branch == "HEAD":
        raise MilestoneError(
            f"refusing to commit on {branch!r}; work happens on a feature branch "
            f"(for V3: `git switch v3`)"
        )
    return branch


def run_checks(checks: list[str]) -> None:
    """Run every check command; the first failure aborts the commit."""
    for command in checks:
        print(f"check: {command}", flush=True)
        completed = subprocess.run(command, shell=True)
        if completed.returncode != 0:
            raise MilestoneError(
                f"check failed with exit {completed.returncode}: {command}\n"
                "nothing was committed; a milestone must be confirmed working first"
            )
    print("all checks passed", flush=True)


def validate_message(subject: str, body: str) -> str:
    if not subject.strip():
        raise MilestoneError("subject must not be empty")
    if "\n" in subject:
        raise MilestoneError("subject must be a single line; put detail in --body")
    if len(subject) > 72:
        raise MilestoneError(f"subject is {len(subject)} characters; keep it at or under 72")
    for label, text in (("subject", subject), ("body", body)):
        hit = FORBIDDEN.search(text)
        if hit:
            raise MilestoneError(f"{label} contains forbidden text {hit.group(0)!r}")
    message = subject.strip()
    if body.strip():
        message += "\n\n" + body.strip()
    return message + "\n"


def staged_paths() -> list[str]:
    out = git("diff", "--cached", "--name-only")
    return [line for line in out.splitlines() if line]


def identity_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME=AUTHOR_NAME,
        GIT_AUTHOR_EMAIL=AUTHOR_EMAIL,
        GIT_COMMITTER_NAME=AUTHOR_NAME,
        GIT_COMMITTER_EMAIL=AUTHOR_EMAIL,
    )
    return env


def write_commit(message: str) -> tuple[str, str]:
    """Create the commit with plumbing and advance HEAD atomically.

    Returns ``(old_head, new_head)``. ``update-ref`` is given the old value so
    a concurrent change to HEAD makes it fail rather than overwrite.
    """
    env = identity_env()
    old_head = git("rev-parse", "HEAD")
    tree = git("write-tree", env=env)
    new_head = git("commit-tree", tree, "-p", old_head, input_text=message, env=env)
    git("update-ref", "-m", "milestone_commit", "HEAD", new_head, old_head, env=env)
    return old_head, new_head


def verify_commit(sha: str) -> None:
    """Read the commit back and confirm identity and message are clean."""
    record = git("log", "-1", "--format=%an%x00%ae%x00%cn%x00%ce%x00%B", sha)
    an, ae, cn, ce, body = record.split("\x00", 4)
    problems = []
    if (an, ae) != (AUTHOR_NAME, AUTHOR_EMAIL):
        problems.append(f"author is {an} <{ae}>")
    if (cn, ce) != (AUTHOR_NAME, AUTHOR_EMAIL):
        problems.append(f"committer is {cn} <{ce}>")
    hit = FORBIDDEN.search(body)
    if hit:
        problems.append(f"message contains {hit.group(0)!r}")
    if problems:
        raise MilestoneError("; ".join(problems))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("-m", "--message", required=True, help="one-line subject")
    parser.add_argument("-b", "--body", default="", help="optional body paragraph(s)")
    parser.add_argument("-a", "--all", action="store_true", help="stage all changes first")
    parser.add_argument(
        "--check",
        action="append",
        default=None,
        metavar="CMD",
        help="verification command; repeatable; replaces the default test run",
    )
    parser.add_argument("--dry-run", action="store_true", help="validate and check, write nothing")
    args = parser.parse_args(argv)

    try:
        root = git("rev-parse", "--show-toplevel")
        os.chdir(root)
        branch = require_feature_branch()
        message = validate_message(args.message, args.body)

        if args.all:
            git("add", "-A")
        paths = staged_paths()
        if not paths:
            raise MilestoneError("nothing is staged; use -a or `git add` first")

        checks = args.check if args.check else DEFAULT_CHECKS
        run_checks(checks)

        if args.dry_run:
            print(f"dry run: would commit {len(paths)} path(s) on {branch}")
            return 0

        old_head, new_head = write_commit(message)
        try:
            verify_commit(new_head)
        except MilestoneError as error:
            git("update-ref", "HEAD", old_head, new_head)
            raise MilestoneError(f"commit rolled back: {error}") from None

        print(f"committed {new_head[:7]} on {branch}: {args.message}")
        print(f"author {AUTHOR_NAME} <{AUTHOR_EMAIL}>, {len(paths)} path(s)")
        return 0
    except MilestoneError as error:
        print(f"milestone_commit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
