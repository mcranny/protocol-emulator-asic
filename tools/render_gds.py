"""Export every GDS layer and hierarchy level to a zoomable vector rendering.

No polygon filtering, geometry simplification, rasterization, or size cutoff is
applied. The GDS remains the authoritative manufacturing artifact. SVG retains
geometry and cell instances, not extracted electrical connectivity.
"""
import argparse
from collections import Counter
import hashlib
import html
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
    controls = "".join(
        f'<label><input type="checkbox" checked data-layer="l{layer}d{datatype}">'
        f'{layer}/{datatype} · {counts[layer, datatype]:,}</label>' for layer, datatype in layers)
    embedded = svg[svg.index("<svg "):]
    page = '''<!doctype html><html lang="en"><meta charset="utf-8">
<title>Full-detail GDS layout</title>
<style>body{margin:0;background:#101827;color:#e5e7eb;font:14px system-ui}
header{padding:14px 20px}h1{font-size:20px;margin:0 0 8px}p{margin:6px 0}
main{display:flex;height:calc(100vh - 125px)}aside{width:210px;overflow:auto;padding:12px}
label{display:block;padding:4px}#viewport{flex:1;overflow:hidden;touch-action:none}
#viewport>svg{width:100%;height:100%;cursor:grab}button{margin-right:10px}
a{color:#93c5fd}</style><header><h1>Full-detail GDS: TOP_NAME</h1>
<p>All LAYER_COUNT layers · POLYGON_COUNT expanded polygons · no geometry simplification.</p>
<p>Scroll to zoom; drag to pan. Layer controls change visibility only.
<button id="reset">Reset view</button><a href="layout.svg">Vector SVG</a> ·
<a href="render-manifest.json">Geometry inventory and source hash</a></p></header>
<main><aside>Layer / datatype · polygon count CONTROLS</aside><div id="viewport">SVG</div></main>
<script>
const svg=document.querySelector('#viewport>svg'), initial=svg.getAttribute('viewBox');
const box=()=>svg.viewBox.baseVal;
document.querySelector('#reset').onclick=()=>svg.setAttribute('viewBox',initial);
document.querySelectorAll('[data-layer]').forEach(input=>input.onchange=()=>{
 svg.querySelectorAll('.'+input.dataset.layer).forEach(p=>p.style.display=input.checked?'':'none');
});
svg.addEventListener('wheel',e=>{e.preventDefault();const b=box(),p=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());
 const k=Math.exp(Math.max(-1,Math.min(1,e.deltaY*.001)));b.x=p.x+(b.x-p.x)*k;b.y=p.y+(b.y-p.y)*k;b.width*=k;b.height*=k;},{passive:false});
let drag=null;
svg.onpointerdown=e=>{drag=[e.clientX,e.clientY];svg.setPointerCapture(e.pointerId);};
svg.onpointermove=e=>{if(!drag)return;const m=svg.getScreenCTM().inverse(),a=new DOMPoint(...drag).matrixTransform(m),p=new DOMPoint(e.clientX,e.clientY).matrixTransform(m),b=box();b.x+=a.x-p.x;b.y+=a.y-p.y;drag=[e.clientX,e.clientY];};
svg.onpointerup=svg.onpointercancel=()=>drag=null;
</script></html>'''
    # Substitute the large SVG last to avoid touching layout text/labels.
    for key, value in (("TOP_NAME", html.escape(top.name)), ("LAYER_COUNT", str(len(layers))),
                       ("POLYGON_COUNT", f"{sum(counts.values()):,}"), ("CONTROLS", controls)):
        page = page.replace(key, value)
    page = page.replace(">SVG</div>", ">" + embedded + "</div>")
    (destination / "index.html").write_text(page)
    result = {"schema": 1, "source": str(source.resolve()),
              "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "top": top.name, "gdstk_version": gdstk.__version__,
              "unit_m": library.unit, "precision_m": library.precision,
              "bounding_box": top.bounding_box(), "geometry_simplified": False,
              "svg_inventory_matches_gds": True,
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
