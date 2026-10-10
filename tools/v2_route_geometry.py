"""Inspect saved guide geometry using the pinned OpenROAD Python interpreter."""
from collections import Counter
import json
from pathlib import Path
import sys


def inspect_block(block):
    units = block.getDbUnitsPerMicron()
    rect = block.getDieArea()
    bounds = [value / units for value in (rect.xMin(), rect.yMin(), rect.xMax(), rect.yMax())]
    layers = [block.getMinRoutingLayer(), block.getMaxRoutingLayer(),
              block.getMinLayerForClock(), block.getMaxLayerForClock()]
    guides = Counter(guide.getLayer().getName() for net in block.getNets() for guide in net.getGuides())
    issues = []
    if bounds != [0, 0, 1289.28, 710.64]:
        issues.append(f"unexpected die geometry: {bounds}")
    if layers != [2, 4, 2, 4]:
        issues.append(f"ODB signal/clock routing limits are not Metal2-Metal4: {layers}")
    if not guides or set(guides) - {"Metal1", "Metal2", "Metal3", "Metal4"}:
        issues.append("missing guides or guides above Metal4")
    # GRT includes Metal1 guides for standard-cell pin access even with a Metal2
    # minimum routing layer. The ODB's stored routing limits govern wire routing.
    return {"die_area_um": bounds, "signal_clock_layer_numbers": layers,
            "guide_count_by_layer": dict(guides), "instance_count": len(block.getInsts()),
            "issues": issues, "passed": not issues}


if __name__ == "__main__":
    import odb

    db = odb.dbDatabase.create()
    odb.read_db(db, sys.argv[1])
    result = inspect_block(db.getChip().getBlock())
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + "\n")
    if not result["passed"]:
        raise SystemExit(2)
