"""Export every GDS layer and hierarchy level to a zoomable vector rendering.

No polygon filtering, geometry simplification, rasterization, or size cutoff is
applied. The GDS remains the authoritative manufacturing artifact. SVG retains
geometry and cell instances, not extracted electrical connectivity.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import gdstk


def inventory(cell, active=None):
    """Count expanded polygons, including paths, arrays and nested references."""
    active = set() if active is None else active
    if cell.name in active:
        raise ValueError("cyclic GDS hierarchy")
    active = active | {cell.name}
    result = Counter((p.layer, p.datatype) for p in cell.get_polygons(depth=0))
    for reference in cell.references:
        if not isinstance(reference.cell, gdstk.Cell):
            raise ValueError(f"unresolved GDS reference: {reference.cell_name}")
        multiplier = max(1, reference.repetition.size)
        for key, count in inventory(reference.cell, active).items():
            result[key] += count * multiplier
    return result


def svg_inventory(path, top):
    """Independently expand SVG cell references and count each exported layer."""
    cells, references, stack = {}, {}, []
    for event, element in ET.iterparse(path, events=("start", "end")):
        tag = element.tag.rsplit('}', 1)[-1]
        if event == 'start':
            if tag == 'g':
                name = element.get('id') or (stack[-1] if stack else None)
                stack.append(name)
                cells.setdefault(name, Counter())
                references.setdefault(name, [])
            elif stack and tag == 'polygon':
                match = re.fullmatch(r'l(\d+)d(\d+)', element.get('class', ''))
                if match is None:
                    raise ValueError('exported polygon has no GDS layer')
                cells[stack[-1]][tuple(map(int, match.groups()))] += 1
            elif stack and tag == 'use':
                href = element.get('{http://www.w3.org/1999/xlink}href', '')
                if not href.startswith('#'):
                    raise ValueError('external or unresolved SVG reference')
                references[stack[-1]].append(href[1:])
        else:
            if tag == 'g': stack.pop()
            element.clear()
    memo = {}
    def expand(name, active):
        if name in active or name not in cells:
            raise ValueError('cyclic or missing SVG cell')
        if name not in memo:
            result = cells[name].copy()
            for child in references[name]: result.update(expand(child, active | {name}))
            memo[name] = result
        return memo[name]
    return expand(top, set())


def render(source, destination, top_name=None):
    source, destination = Path(source), Path(destination)
    library = gdstk.read_gds(source)
    tops = library.top_level()
    if top_name is None and len(tops) != 1:
        raise ValueError("GDS must have one top cell or specify --top")
    matches = [c for c in library.cells if c.name == top_name] if top_name else tops
    if len(matches) != 1:
        raise ValueError("requested top cell is missing or ambiguous")
    top = matches[0]
    counts = inventory(top)
    if not counts or top.bounding_box() is None:
        raise ValueError("empty GDS layout")
    destination.mkdir(parents=True, exist_ok=False)
    palette = ("#60a5fa", "#fb923c", "#4ade80", "#f472b6", "#facc15", "#a78bfa", "#22d3ee")
    layers = sorted(counts)
    styles = {key: {"fill": palette[i % len(palette)], "fill-opacity": "0.65", "stroke": "none"}
              for i, key in enumerate(layers)}
    svg_path = destination / "layout.svg"
    # 17 decimal places retain double precision at the GDS user-unit scale.
    # Reference transforms and repetitions are retained by gdstk's exporter.
    top.write_svg(svg_path, scaling=1, precision=17, shape_style=styles, pad=0,
                  background="#101827")
    svg = svg_path.read_text()
    # gdstk assigns nondeterministic pointer IDs to polygons. They are unused
    # by references (which target cell names); omit only those metadata IDs.
    svg = re.sub(r'(<polygon) id="0x[0-9a-fA-F]+"', r'\1', svg)
    svg_path.write_text(svg)
    if svg_inventory(svg_path, top.name) != counts:
        raise ValueError("SVG geometry inventory differs from source GDS")
    # Keep the browser document bounded. Full geometry remains in layout.svg;
    # the local service redraws each viewport directly from the original GDS.
    (destination / "index.html").write_bytes(
        Path(__file__).with_name("gds_viewer.html").read_bytes())
    result = {"schema": 1, "source": str(source.resolve()),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "top": top.name, "gdstk_version": gdstk.__version__,
              "unit_m": library.unit, "precision_m": library.precision,
              "bounding_box": top.bounding_box(), "geometry_simplified": False,
              "svg_inventory_matches_gds": True,
              "viewer": {"mode": "native-viewport", "embedded_geometry": False,
                         "requires_local_service": True,
                         "overview": "area-density", "detail": "literal-geometry"},
              "hierarchy_depth_limit": None, "layers_omitted": [],
              "expanded_polygons": sum(counts.values()),
              "layers": [{"layer": layer, "datatype": datatype, "polygons": counts[layer, datatype]}
                         for layer, datatype in layers],
              "svg_sha256": hashlib.sha256(svg_path.read_bytes()).hexdigest()}
    (destination / "render-manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("gds", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--top")
    args = parser.parse_args()
    print(json.dumps(render(args.gds, args.output, args.top), indent=2))
