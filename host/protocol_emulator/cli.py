"""V3 command-line entry point."""
import argparse

from . import v3


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and run the requested subcommand."""
    parser = argparse.ArgumentParser(prog="protocol-emulator")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("version")
    args = parser.parse_args(argv)
    if args.command == "version":
        print(v3.__version__)
        return 0
    parser.error(f"unknown command {args.command!r}")


if __name__ == "__main__":
    raise SystemExit(main())
