"""The viewport must preserve tiny geometry and bound per-request resources."""

import json
import threading
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import gdstk
import klayout.db as db
import klayout.lay as lay
import numpy as np
import pytest

from tools.gds_viewer_geometry import (
    BACKGROUND,
    composite,
    coverage_grid,
    layer_names,
)
from tools.render_gds import render
from tools.view_gds import (
    MAX_PIXELS,
    MAX_SIDE,
    LayoutRenderer,
    make_handler,
    parse_view,
)


@pytest.fixture
def source(tmp_path):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    leaf = lib.new_cell("tiny_via")
    leaf.add(gdstk.rectangle((0, 0), (0.001, 0.002), layer=19, datatype=7))
    child = lib.new_cell("array")
    child.add(gdstk.Reference(leaf, columns=3, rows=2, spacing=(0.01, 0.01)))
    top = lib.new_cell("top")
    top.add(
        gdstk.Reference(
            child, origin=(2, 3), rotation=1.5707963267948966, x_reflection=True
        )
    )
    top.add(gdstk.rectangle((-1, -1), (6, 6), layer=189, datatype=4))
    top.add(gdstk.FlexPath([(0, 0), (5, 0), (5, 5)], 0.01, simple_path=True, layer=8))
    p = tmp_path / "test.gds"
    lib.write_gds(p)
    return p


def png(renderer, box, layers, size=100):
    return lay.PixelBuffer.from_png_data(
        renderer.render(
            f"box={','.join(map(str, box))}&width={size}&height={size}&layers={layers}"
        )
    )


def pixel_at(image, box, point):
    x = int((point[0] - box[0]) / (box[2] - box[0]) * image.width())
    y = int((box[3] - point[1]) / (box[3] - box[1]) * image.height())
    return image.pixel(x, y) & 0xFFFFFF


def test_transformed_array_and_smallest_grid_geometry_are_visible(source):
    renderer = LayoutRenderer(source)
    assert renderer.metadata["full_hierarchy"]
    assert renderer.metadata["small_geometry_filter"] is False
    assert renderer.view.max_hier_levels == 3
    # Read the independent expanded GDS polygons, then zoom to every transformed
    # 1 nm via. This checks the native path, not just a configuration flag.
    top = gdstk.read_gds(source).top_level()[0]
    vias = [p for p in top.get_polygons() if p.layer == 19]
    assert len(vias) == 6
    for via in vias:
        centre = via.points.mean(axis=0)
        box = [
            centre[0] - 0.005,
            centre[1] - 0.005,
            centre[0] + 0.005,
            centre[1] + 0.005,
        ]
        image = png(renderer, box, "19/7")
        # Patterned fills have intentional holes; test the complete feature
        # footprint, including its unsimplified boundary.
        assert any(
            image.pixel(x, y) & 0xFFFFFF != 0x101827
            for x in range(38, 62)
            for y in range(38, 62)
        )
        assert pixel_at(image, box, (box[0] + 0.0001, box[1] + 0.0001)) == 0x101827
        hidden = png(renderer, box, "")
        assert all(
            hidden.pixel(x, y) & 0xFFFFFF == 0x101827
            for x in range(38, 62)
            for y in range(38, 62)
        )
        boundary = png(renderer, box, "189/4")
        combined = png(renderer, box, "189/4,19/7")
        assert any(
            boundary.pixel(x, y) != combined.pixel(x, y)
            for x in range(38, 62)
            for y in range(38, 62)
        )
    wire_box = [2.48, -0.02, 2.52, 0.02]
    wire = png(renderer, wire_box, "8/0")
    assert any(
        wire.pixel(x, y) & 0xFFFFFF != 0x101827
        for x in range(10, 90)
        for y in range(36, 64)
    )
    assert pixel_at(wire, wire_box, (2.5, 0.015)) == 0x101827


@pytest.mark.parametrize(
    "query",
    [
        "box=0,0,1,1&width=100000&height=100000&layers=8/0",
        "box=0,0,1,1&width=0&height=1&layers=8/0",
        "box=0,0,nan,1&width=10&height=10&layers=8/0",
        "box=0,0,inf,1&width=10&height=10&layers=8/0",
        "box=0,0,-1,1&width=10&height=10&layers=8/0",
        "box=0,0,1,1&width=10&height=20&layers=8/0",
        "box=0,0,1,1&width=10&height=10&layers=unknown",
        "box=0,0,1,1&width=10&height=10&layers=8/0&width=10",
        "box=0,0,1,1&width=10&height=10",
        "box=0,0,1,1&width=10&height=10&layers=&mode=invalid",
        "box=0,0,1,1&width=10&height=10&layers=&pixel_ratio=nan",
        "box=0,0,1,1&width=10&height=10&layers=&pixel_ratio=0",
    ],
    ids=[
        "query=box=0,0,1,1&width=100000&height=100000&layers=8/0",
        "query=box=0,0,1,1&width=0&height=1&layers=8/0",
        "query=box=0,0,nan,1&width=10&height=10&layers=8/0",
        "query=box=0,0,inf,1&width=10&height=10&layers=8/0",
        "query=box=0,0,-1,1&width=10&height=10&layers=8/0",
        "query=box=0,0,1,1&width=10&height=20&layers=8/0",
        "query=box=0,0,1,1&width=10&height=10&layers=unknown",
        "query=box=0,0,1,1&width=10&height=10&layers=8/0&width=10",
        "query=box=0,0,1,1&width=10&height=10",
        "query=box=0,0,1,1&width=10&height=10&layers=&mode=invalid",
        "query=box=0,0,1,1&width=10&height=10&layers=&pixel_ratio=nan",
        "query=box=0,0,1,1&width=10&height=10&layers=&pixel_ratio=0",
    ],
)
def test_invalid_or_unbounded_view_is_rejected(query):
    with pytest.raises(ValueError):
        parse_view(query, {"8/0"})


def test_maximum_view_and_empty_layer_selection():
    _, w, h, selected = parse_view(
        f"box=0,0,1,1&width={MAX_SIDE}&height={MAX_SIDE}&layers=", {"8/0"}
    )
    assert w * h <= MAX_PIXELS and selected == set()


def test_matching_bundle_and_lightweight_export(source, tmp_path):
    out = tmp_path / "export"
    report = render(source, out)
    page = (out / "index.html").read_bytes()
    assert len(page) < 30_000 and b"<polygon" not in page and b"<svg" not in page
    renderer = LayoutRenderer(source, render_dir=out)
    assert renderer.metadata["svg_available"]
    assert renderer.metadata["source_sha256"] == report["source_sha256"]
    (out / "layout.svg").write_text("modified")
    with pytest.raises(ValueError, match="checksum"):
        LayoutRenderer(source, render_dir=out)


def test_wrong_source_bundle_rejected(source, tmp_path):
    out = tmp_path / "export"
    render(source, out)
    lib = gdstk.Library()
    lib.new_cell("other").add(gdstk.rectangle((0, 0), (1, 1)))
    other = tmp_path / "other.gds"
    lib.write_gds(other)
    with pytest.raises(ValueError, match="does not match"):
        LayoutRenderer(other, render_dir=out)


def test_top_selection(tmp_path):
    lib = gdstk.Library()
    for name in ("a", "b"):
        lib.new_cell(name).add(gdstk.rectangle((0, 0), (1, 1)))
    p = tmp_path / "multi.gds"
    lib.write_gds(p)
    with pytest.raises(ValueError, match="one top"):
        LayoutRenderer(p)
    assert LayoutRenderer(p, top_name="b").metadata["top"] == "b"


def test_http_routes_and_host_boundary(source):
    # The production renderer and HTTP server share one thread. This test
    # likewise constructs the renderer inside the serving thread.
    ready = threading.Event()
    state = {}

    def run():
        renderer = LayoutRenderer(source)
        with HTTPServer(("127.0.0.1", 0), make_handler(renderer)) as server:
            state["server"] = server
            ready.set()
            server.serve_forever(poll_interval=0.01)

    thread = threading.Thread(target=run)
    thread.start()
    assert ready.wait(10)
    server = state["server"]
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(url + "/api/metadata") as response:
            assert json.load(response)["top"] == "top"
        with urlopen(
            url + "/api/render?box=0,0,5,5&width=100&height=100&layers=8/0"
        ) as response:
            assert response.headers["Content-Type"] == "image/png"
            assert response.headers["Cache-Control"] == "no-store"
            assert response.read().startswith(b"\x89PNG")
        with urlopen(url + "/layout.gds") as response:
            assert "attachment" in response.headers["Content-Disposition"]
            assert response.read() == source.read_bytes()
        for route, code in [
            ("/../private", 404),
            ("/layout.svg", 404),
            ("/api/render?width=100000", 400),
        ]:
            with pytest.raises(HTTPError) as error:
                urlopen(url + route)
            assert error.value.code == code
        with pytest.raises(HTTPError) as error:
            urlopen(
                Request(url + "/api/metadata", headers={"Host": "untrusted.example"})
            )
        assert error.value.code == 403
    finally:
        server.shutdown()
        thread.join(10)


def test_density_captures_subpixel_wire_and_counts_overlap():
    layout = db.Layout()
    layout.dbu = 0.001
    top = layout.create_cell("top")
    index = layout.layer(8, 0)
    # 1 nm wire occupies 1/1000 of a 1 um bucket, too thin for literal overview.
    top.shapes(index).insert(db.Box(0, 0, 1000, 1))
    coverage = coverage_grid(db, top, index, (0, 0, 2, 2), layout.dbu, 2, 2)
    np.testing.assert_allclose(coverage, [[0, 0], [0.001, 0]])
    top.shapes(index).insert(db.Box(0, 0, 1000, 1))
    doubled = coverage_grid(db, top, index, (0, 0, 2, 2), layout.dbu, 2, 2)
    np.testing.assert_allclose(doubled, coverage * 2)
    pixels = composite([1.8 * np.sqrt(np.log1p(coverage))], [(240, 100, 180)], (2, 2))
    assert tuple(pixels[1, 0]) != BACKGROUND
    assert tuple(pixels[0, 0]) == BACKGROUND


def test_density_keeps_transformed_arrays_and_clips_outside(source):
    renderer = LayoutRenderer(source)
    index = renderer.layer_indices["19/7"]
    grid = coverage_grid(
        db, renderer.top, index, (1, 2, 3, 4), renderer.layout.dbu, 100, 100
    )
    # Six 1x2 nm rectangles, each bucket 20x20 nm.
    assert float(grid.sum()) == pytest.approx(12 / 400)
    outside = coverage_grid(
        db, renderer.top, index, (20, 20, 22, 22), renderer.layout.dbu, 100, 100
    )
    assert not outside.any()


def test_feature_limits_follow_geometry_and_magnification(tmp_path):
    library = gdstk.Library(unit=1e-6, precision=1e-9)
    leaf = library.new_cell("leaf")
    leaf.add(gdstk.rectangle((0, 0), (0.02, 0.06), layer=1))
    leaf.add(gdstk.FlexPath([(1, 0), (2, 0)], 0.01, simple_path=True, layer=2))
    top = library.new_cell("top")
    top.add(gdstk.Reference(leaf, magnification=0.5, columns=2, rows=2, spacing=(3, 3)))
    # Unreferenced geometry must not set the top's zoom limit.
    library.new_cell("unrelated").add(gdstk.rectangle((0, 0), (0.001, 0.001), layer=1))
    path = tmp_path / "scaled.gds"
    library.write_gds(path)
    renderer = LayoutRenderer(path, top_name="top")
    assert renderer.metadata["minimum_feature_um"] == pytest.approx(0.005)
    assert renderer.metadata["minimum_view_span_um"] == pytest.approx(0.020)
    assert renderer.scales["1/0"]["typical_um"] == pytest.approx(0.010)


def test_auto_lod_switch_and_manual_override(source):
    renderer = LayoutRenderer(source)
    assert renderer.view_mode((0, 0, 10, 10), 100, {"19/7"}, "auto", 1) == "density"
    assert (
        renderer.view_mode((0, 0, 0.01, 0.01), 100, {"19/7"}, "auto", 1) == "geometry"
    )
    assert (
        renderer.view_mode((0, 0, 10, 10), 100, {"19/7"}, "geometry", 1) == "geometry"
    )
    assert (
        renderer.view_mode((0, 0, 0.01, 0.01), 100, {"19/7"}, "density", 1) == "density"
    )


def test_density_cache_is_one_view_and_geometry_clears_visibility(source):
    renderer = LayoutRenderer(source)
    q = "box=0,0,10,10&width=100&height=100&mode=density&layers="
    renderer.render(q + "8/0")
    renderer.render(q + "19/7")
    assert set(renderer.density_cache) == {"8/0", "19/7"}
    renderer.render(q.replace("0,0,10,10", "1,1,11,11") + "19/7")
    assert set(renderer.density_cache) == {"19/7"}
    data = renderer.render(q.replace("mode=density", "mode=geometry"))
    image = lay.PixelBuffer.from_png_data(data)
    assert image.pixel(50, 50) & 0xFFFFFF == 0x101827


def test_layer_names_from_lyp_and_dark_empty_background(source, tmp_path):
    profile = tmp_path / "test.lyp"
    profile.write_text(
        "<layer-properties><properties><name>Metal2</name><source>8/0@1</source>"
        "</properties></layer-properties>"
    )
    assert layer_names(profile) == {"8/0": "Metal2"}
    renderer = LayoutRenderer(source, layer_properties=profile)
    assert renderer.layer_metadata["8/0"]["name"] == "Metal2"
    assert renderer.metadata["placement_grid"] is False
    for mode in ("geometry", "density"):
        data = renderer.render(f"box=0,0,10,10&width=10&height=10&layers=&mode={mode}")
        image = lay.PixelBuffer.from_png_data(data)
        assert all(
            image.pixel(x, y) & 0xFFFFFF == 0x101827
            for x in range(10)
            for y in range(10)
        )
