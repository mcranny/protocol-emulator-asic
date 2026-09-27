"""Rendering must retain tiny routing, vias, arrays and all hierarchy levels."""
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import gdstk
import pytest

spec = importlib.util.spec_from_file_location("render_gds", Path(__file__).parents[1] / "tools/render_gds.py")
renderer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(renderer)


def test_full_geometry_export(tmp_path):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    leaf = lib.new_cell("via")
    leaf.add(gdstk.rectangle((0, 0), (.001, .002), layer=19, datatype=7))
    child = lib.new_cell("routing")
    child.add(gdstk.Reference(leaf, columns=3, rows=2, spacing=(.05, .08)))
    child.add(gdstk.FlexPath([(0, 0), (2, 0), (2, 1)], .01, simple_path=True, layer=8))
    top = lib.new_cell("top")
    top.add(gdstk.Reference(child, origin=(5, 6), rotation=1.5707963267948966))
    top.add(gdstk.Reference(child, origin=(10, 8), x_reflection=True))
    top.add(gdstk.rectangle((0, 0), (20, 10), layer=999))
    source = tmp_path / "test.gds"
    lib.write_gds(source)
    report = renderer.render(source, tmp_path / "render")
    assert report["expanded_polygons"] == 15
    assert report["layers"] == [
        {"layer": 8, "datatype": 0, "polygons": 2},
        {"layer": 19, "datatype": 7, "polygons": 12},
        {"layer": 999, "datatype": 0, "polygons": 1},
    ]
    svg = ET.parse(tmp_path / "render/layout.svg")
    ns = {"s": "http://www.w3.org/2000/svg"}
    polys = svg.findall(".//s:polygon", ns)
    assert {p.attrib["class"] for p in polys} == {"l8d0", "l19d7", "l999d0"}
    tiny = next(p for p in polys if p.attrib["class"] == "l19d7")
    points = [tuple(map(float, pair.split(','))) for pair in tiny.attrib["points"].split()]
    assert set(points) == {(0, 0), (.001, 0), (.001, .002), (0, .002)}
    uses = svg.findall(".//s:use", ns)
    assert len(uses) == 8  # six array elements and two transformed parent instances
    ids = {e.get("id") for e in svg.iter() if e.get("id")}
    assert all(e.get("{http://www.w3.org/1999/xlink}href")[1:] in ids for e in uses)
    page = (tmp_path / "render/index.html").read_text()
    assert "<canvas" in page and "<polygon" not in page
    assert len(page.encode()) < 30_000
    assert report["viewer"]["embedded_geometry"] is False
    assert "0x" not in (tmp_path / "render/layout.svg").read_text()
    assert report["layers_omitted"] == [] and report["geometry_simplified"] is False
    assert report["svg_inventory_matches_gds"] is True
    with pytest.raises(FileExistsError):
        renderer.render(source, tmp_path / "render")


def test_multiple_tops_need_explicit_selection(tmp_path):
    lib = gdstk.Library()
    for name in ("a", "b"):
        lib.new_cell(name).add(gdstk.rectangle((0, 0), (1, 1)))
    source = tmp_path / "test.gds"
    lib.write_gds(source)
    with pytest.raises(ValueError, match="one top"):
        renderer.render(source, tmp_path / "ambiguous")
    assert renderer.render(source, tmp_path / "selected", "b")["top"] == "b"


def test_hex_cell_names_are_not_discarded(tmp_path):
    lib = gdstk.Library()
    leaf = lib.new_cell('0xabc')
    leaf.add(gdstk.rectangle((0, 0), (1, 1)))
    top = lib.new_cell('0xdef')
    top.add(gdstk.Reference(leaf))
    source = tmp_path / 'hex.gds'
    lib.write_gds(source)
    renderer.render(source, tmp_path / 'out')
    assert renderer.svg_inventory(tmp_path / 'out/layout.svg', '0xdef') == {(0, 0): 1}
