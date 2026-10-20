"""Windows entry point for the repository's Python test target.

GNU make is installed inside WSL, not on Windows. ``make test`` and this
module run the same pytest command. Simulation Makefiles stay in WSL.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def pytest_command():
    """Argv for the ``python-test`` recipe in the root Makefile."""
    return [
        sys.executable,
        "-m",
        "pytest",
        "-q",
        "--junitxml=build/python-results.xml",
    ]


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if args not in ([], ["test"], ["python-test"]):
        print(
            "Only the pytest target runs without GNU make. "
            "Simulation Makefiles run inside WSL.",
            file=sys.stderr,
        )
        return 2
    return subprocess.call(pytest_command(), cwd=ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
