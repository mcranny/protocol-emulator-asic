"""The viewport must preserve tiny geometry and bound per-request resources."""
from http.server import HTTPServer
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import gdstk
import klayout.lay as lay
import pytest

from tools.view_gds import LayoutRenderer, MAX_PIXELS, MAX_SIDE, make_handler, parse_view
from tools.render_gds import render


@pytest.fixture
def source(tmp_path):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    leaf = lib.new_cell('tiny_via')
    leaf.add(gdstk.rectangle((0, 0), (.001, .002), layer=19, datatype=7))
    child = lib.new_cell('array')
    child.add(gdstk.Reference(leaf, columns=3, rows=2, spacing=(.01, .01)))
    top = lib.new_cell('top')
    top.add(gdstk.Reference(child, origin=(2, 3), rotation=1.5707963267948966, x_reflection=True))
    top.add(gdstk.rectangle((-1, -1), (6, 6), layer=189, datatype=4))
    top.add(gdstk.FlexPath([(0, 0), (5, 0), (5, 5)], .01, simple_path=True, layer=8))
    p = tmp_path / 'test.gds'
    lib.write_gds(p)
    return p


def png(renderer, box, layers, size=100):
    return lay.PixelBuffer.from_png_data(renderer.render(
        f'box={",".join(map(str, box))}&width={size}&height={size}&layers={layers}'))


def pixel_at(image, box, point):
    x = int((point[0] - box[0]) / (box[2] - box[0]) * image.width())
    y = int((box[3] - point[1]) / (box[3] - box[1]) * image.height())
    return image.pixel(x, y) & 0xffffff


def test_transformed_array_and_smallest_grid_geometry_are_visible(source):
    renderer = LayoutRenderer(source)
    assert renderer.metadata['full_hierarchy']
    assert renderer.metadata['small_geometry_filter'] is False
    assert renderer.view.max_hier_levels == 3
    # Read the independent expanded GDS polygons, then zoom to every transformed
    # 1 nm via. This checks the native path, not just a configuration flag.
    top = gdstk.read_gds(source).top_level()[0]
    vias = [p for p in top.get_polygons() if p.layer == 19]
    assert len(vias) == 6
    for via in vias:
        centre = via.points.mean(axis=0)
        box = [centre[0]-.005, centre[1]-.005, centre[0]+.005, centre[1]+.005]
        image = png(renderer, box, '19/7')
        # Patterned fills have intentional holes; test the complete feature
        # footprint, including its unsimplified boundary.
        assert any(image.pixel(x, y) & 0xffffff != 0x101827
                   for x in range(38, 62) for y in range(38, 62))
        assert pixel_at(image, box, (box[0]+.0001, box[1]+.0001)) == 0x101827
        hidden = png(renderer, box, '')
        assert all(hidden.pixel(x, y) & 0xffffff == 0x101827
                   for x in range(38, 62) for y in range(38, 62))
        boundary = png(renderer, box, '189/4')
        combined = png(renderer, box, '189/4,19/7')
        assert any(boundary.pixel(x, y) != combined.pixel(x, y)
                   for x in range(38, 62) for y in range(38, 62))
    wire_box = [2.48, -.02, 2.52, .02]
    wire = png(renderer, wire_box, '8/0')
    assert any(wire.pixel(x, y) & 0xffffff != 0x101827
               for x in range(10, 90) for y in range(36, 64))
    assert pixel_at(wire, wire_box, (2.5, .015)) == 0x101827


@pytest.mark.parametrize('query', [
    'box=0,0,1,1&width=100000&height=100000&layers=8/0',
    'box=0,0,1,1&width=0&height=1&layers=8/0',
    'box=0,0,nan,1&width=10&height=10&layers=8/0',
    'box=0,0,inf,1&width=10&height=10&layers=8/0',
    'box=0,0,-1,1&width=10&height=10&layers=8/0',
    'box=0,0,1,1&width=10&height=20&layers=8/0',
    'box=0,0,1,1&width=10&height=10&layers=unknown',
    'box=0,0,1,1&width=10&height=10&layers=8/0&width=10',
    'box=0,0,1,1&width=10&height=10',
])
def test_invalid_or_unbounded_view_is_rejected(query):
    with pytest.raises(ValueError):
        parse_view(query, {'8/0'})


def test_maximum_view_and_empty_layer_selection():
    _, w, h, selected = parse_view(f'box=0,0,1,1&width={MAX_SIDE}&height={MAX_SIDE}&layers=', {'8/0'})
    assert w*h <= MAX_PIXELS and selected == set()


def test_matching_bundle_and_lightweight_export(source, tmp_path):
    out = tmp_path/'export'
    report = render(source, out)
    page = (out/'index.html').read_bytes()
    assert len(page) < 30_000 and b'<polygon' not in page and b'<svg' not in page
    renderer = LayoutRenderer(source, render_dir=out)
    assert renderer.metadata['svg_available']
    assert renderer.metadata['source_sha256'] == report['source_sha256']
    (out/'layout.svg').write_text('modified')
    with pytest.raises(ValueError, match='checksum'):
        LayoutRenderer(source, render_dir=out)


def test_wrong_source_bundle_rejected(source, tmp_path):
    out = tmp_path/'export'
    render(source, out)
    lib = gdstk.Library(); lib.new_cell('other').add(gdstk.rectangle((0, 0), (1, 1)))
    other = tmp_path/'other.gds'; lib.write_gds(other)
    with pytest.raises(ValueError, match='does not match'):
        LayoutRenderer(other, render_dir=out)


def test_top_selection(tmp_path):
    lib = gdstk.Library()
    for name in ('a', 'b'):
        lib.new_cell(name).add(gdstk.rectangle((0, 0), (1, 1)))
    p = tmp_path/'multi.gds'; lib.write_gds(p)
    with pytest.raises(ValueError, match='one top'):
        LayoutRenderer(p)
    assert LayoutRenderer(p, top_name='b').metadata['top'] == 'b'


def test_http_routes_and_host_boundary(source):
    # The production renderer and HTTP server share one thread. This test
    # likewise constructs the renderer inside the serving thread.
    ready = threading.Event(); state = {}
    def run():
        renderer = LayoutRenderer(source)
        with HTTPServer(('127.0.0.1', 0), make_handler(renderer)) as server:
            state['server'] = server; ready.set(); server.serve_forever(poll_interval=.01)
    thread = threading.Thread(target=run); thread.start(); assert ready.wait(10)
    server = state['server']; url = f'http://127.0.0.1:{server.server_port}'
    try:
        with urlopen(url+'/api/metadata') as response:
            assert json.load(response)['top'] == 'top'
        with urlopen(url+'/api/render?box=0,0,5,5&width=100&height=100&layers=8/0') as response:
            assert response.headers['Content-Type'] == 'image/png'
            assert response.headers['Cache-Control'] == 'no-store'
            assert response.read().startswith(b'\x89PNG')
        with urlopen(url+'/layout.gds') as response:
            assert 'attachment' in response.headers['Content-Disposition']
            assert response.read() == source.read_bytes()
        for route, code in [('/../private', 404), ('/layout.svg', 404), ('/api/render?width=100000', 400)]:
            with pytest.raises(HTTPError) as error:
                urlopen(url+route)
            assert error.value.code == code
        with pytest.raises(HTTPError) as error:
            urlopen(Request(url+'/api/metadata', headers={'Host': 'untrusted.example'}))
        assert error.value.code == 403
    finally:
        server.shutdown(); thread.join(10)
