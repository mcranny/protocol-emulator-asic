"""Write an Icarus view of the CMOS5L standard-cell Verilog for gate-level runs.

The cell models feed their function from ``delayed_*`` nets that only the
specify block's timing checks drive. Icarus does not drive them, so every flop
stays X. This drops the specify blocks and reads the ports directly, which
gives a zero-delay functional view of the same cells.
"""
import argparse
import re
from pathlib import Path


def functional_view(source: str) -> str:
    """Return ``source`` with specify blocks removed and delayed nets undone."""
    source = re.sub(r"\bspecify\b.*?\bendspecify\b", "", source, flags=re.S)
    return re.sub(r"\bdelayed_([A-Za-z_][A-Za-z0-9_]*)", r"\1", source)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("source", type=Path, help="sg13cmos5l_stdcell.v from the PDK")
    parser.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    view = functional_view(args.source.read_text())
    if "specify" in view or "delayed_" in view:
        parser.error(f"{args.source}: specify blocks or delayed nets remain")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(view)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
