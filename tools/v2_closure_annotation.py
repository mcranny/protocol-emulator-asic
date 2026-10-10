"""Audit pre-route unannotated drivers against actual ODB connectivity.

Run with the pinned OpenROAD Python interpreter. Unlike a post-route wire filter,
this checks pin connectivity: global routes do not yet have detailed wires.
"""
import json
from pathlib import Path
import re
import sys


def unannotated_drivers(report):
    section = report.split("report_parasitic_annotation -report_unannotated\n", 1)[1]
    match = re.search(r"Found (\d+) unannotated drivers\.\n(.*?)"
                      r"Found (\d+) partially unannotated drivers\.", section, re.S)
    if match is None:
        raise ValueError("missing parasitic annotation counts")
    drivers = [line.strip() for line in match[2].splitlines() if line.strip()]
    if len(drivers) != int(match[1]):
        raise ValueError("unannotated driver count does not match report")
    return drivers, int(match[3])


def audit(block, report):
    drivers, partial = unannotated_drivers(report)
    ignored, connected, unknown = [], [], []
    for name in drivers:
        pin = block.findITerm(name) if "/" in name else block.findBTerm(name)
        if pin is None:
            unknown.append(name)
            continue
        net = pin.getNet()
        if net is None or len(net.getITerms()) + len(net.getBTerms()) <= 1:
            ignored.append(name)
        else:
            connected.append(name)
    return {"unannotated_drivers": drivers, "unconnected_drivers": ignored,
            "connected_unannotated_drivers": connected, "unknown_drivers": unknown,
            "partially_unannotated_driver_count": partial,
            "passed": not (connected or unknown or partial)}


if __name__ == "__main__":
    import odb

    database = odb.dbDatabase.create()
    odb.read_db(database, sys.argv[1])
    result = audit(database.getChip().getBlock(), Path(sys.argv[2]).read_text())
    Path(sys.argv[3]).write_text(json.dumps(result, indent=2) + "\n")
