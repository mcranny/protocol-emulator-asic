"""Serve a full-detail GDS viewport without sending layout geometry to the browser.

KLayout keeps the original hierarchy locally and rasterizes the requested view
at its requested pixel density. Auto mode integrates polygon area for a density
overview and switches to literal geometry when features become resolvable. The complete GDS/SVG remains downloadable.
"""
import argparse
import hashlib
from io import BytesIO
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
from pathlib import Path
import shutil
import sys
from urllib.parse import parse_qs, urlsplit

import numpy as np
from PIL import Image

try:
    from .gds_viewer_geometry import BACKGROUND, DENSITY_SIDE, composite, coverage_grid, feature_scales, layer_names
except ImportError:
    from gds_viewer_geometry import BACKGROUND, DENSITY_SIDE, composite, coverage_grid, feature_scales, layer_names

MAX_SIDE = 2048
MAX_PIXELS = 4_194_304
PALETTE = (0x60A5FA, 0xFB923C, 0x4ADE80, 0xF472B6, 0xFACC15, 0xA78BFA, 0x22D3EE)


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def viewer_page():
    return Path(__file__).with_name('gds_viewer.html').read_bytes()


def parse_view(query, available):
    """Bound allocation, reject invalid coordinates and unknown layer selections."""
    values = parse_qs(query, keep_blank_values=True, strict_parsing=True)
    required = {'box', 'width', 'height', 'layers'}
    if not required <= set(values) or set(values) - required - {'mode', 'pixel_ratio'} or any(len(v) != 1 for v in values.values()):
        raise ValueError('Expected one box, width, height and layers parameter')
    if values.get('mode', ['auto'])[0] not in {'auto', 'density', 'geometry'}:
        raise ValueError('Unknown view mode')
    ratio = float(values.get('pixel_ratio', ['1'])[0])
    if not math.isfinite(ratio) or not .1 <= ratio <= 8:
        raise ValueError('Invalid pixel ratio')
    width, height = int(values['width'][0]), int(values['height'][0])
    if not (1 <= width <= MAX_SIDE and 1 <= height <= MAX_SIDE and width * height <= MAX_PIXELS):
        raise ValueError('Viewport exceeds pixel budget')
    box = tuple(float(v) for v in values['box'][0].split(','))
    if len(box) != 4 or not all(math.isfinite(v) and abs(v) <= 1e9 for v in box):
        raise ValueError('Invalid view coordinates')
    if box[2] <= box[0] or box[3] <= box[1]:
        raise ValueError('View must have positive width and height')
    # Native rendering fits its target box to the image aspect ratio. Require
    # the client to do the same, so pan coordinates always match rendered data.
    if not math.isclose((box[2] - box[0]) / (box[3] - box[1]), width / height, rel_tol=1e-5):
        raise ValueError('View and image aspect ratios differ')
    selected = set(filter(None, values['layers'][0].split(',')))
    if not selected <= set(available):
        raise ValueError('Unknown layer')
    return box, width, height, selected


class LayoutRenderer:
    def __init__(self, source, top_name=None, render_dir=None, layer_properties=None):
        import klayout.db as db
        import klayout.lay as lay

        self.db = db
        self.source = Path(source).resolve(strict=True)
        self.render_dir = Path(render_dir).resolve(strict=True) if render_dir else None
        source_hash = checksum(self.source)
        if self.render_dir:
            manifest = json.loads((self.render_dir / 'render-manifest.json').read_text())
            if manifest['source_sha256'] != source_hash:
                raise ValueError('Render bundle does not match this GDS')
            if top_name and top_name != manifest['top']:
                raise ValueError('Requested top differs from render bundle')
            top_name = manifest['top']
            if checksum(self.render_dir / 'layout.svg') != manifest['svg_sha256']:
                raise ValueError('Vector SVG checksum differs from render bundle')
        self.view = lay.LayoutView()
        self.view.load_layout(str(self.source), False)
        layout = self.view.active_cellview().layout()
        tops = list(layout.top_cells())
        top = layout.cell(top_name) if top_name else (tops[0] if len(tops) == 1 else None)
        if top is None:
            raise ValueError('GDS must have one top cell or specify --top')
        self.view.select_cell(top.cell_index(), 0)
        self.layout, self.top = layout, top
        names = layer_names(layer_properties)
        self.scales = feature_scales(layout, top)
        self.density_cache_key, self.density_cache = None, {}
        self.last_mode = 'geometry'
        bounds = top.dbbox()
        if bounds.empty() or bounds.width() <= 0 or bounds.height() <= 0:
            raise ValueError('Empty GDS layout')
        self.view.min_hier_levels = 0
        # Derive the depth from the actual hierarchy; there is no fixed cutoff.
        self.view.max_hier_levels = top.hierarchy_levels() + 1
        for key, value in {'abstract-mode-enabled': 'false', 'drop-small-cells': 'false',
                           'text-lazy-rendering': 'false', 'background-color': '#000000',
                           'grid-visible': 'false', 'drawing-workers': '1'}.items():
            self.view.set_config(key, value)
        self.view.add_missing_layers()
        self.layers = {}
        layers = []
        for i, layer in enumerate(self.view.each_layer()):
            key = f'{layer.source_layer}/{layer.source_datatype}'
            color = PALETTE[i % len(PALETTE)]
            layer.visible = True
            # Render grayscale masks, then blend colors with actual opacity.
            # Solid fills replace the bright stipple dots; outlines remain thin.
            layer.fill_color = 0x707070
            layer.frame_color = 0xB0B0B0
            layer.dither_pattern = 0
            layer.transparent = False
            self.layers[key] = layer
            layers.append({'key': key, 'color': f'#{color:06x}',
                           'name': names.get(key, layer.name or key),
                           'feature': self.scales.get(key)})
        self.layer_metadata = {layer['key']: layer for layer in layers}
        self.layer_indices = {f'{layout.get_info(i).layer}/{layout.get_info(i).datatype}': i
                              for i in layout.layer_indexes()}
        minimum = min((x['minimum_um'] for x in self.scales.values()), default=layout.dbu)
        self.metadata = {
            'top': top.name, 'bounds': [bounds.left, bounds.bottom, bounds.right, bounds.top],
            'dbu_um': layout.dbu, 'source_sha256': source_hash, 'layers': layers,
            'max_side': MAX_SIDE, 'max_pixels': MAX_PIXELS,
            'full_hierarchy': True, 'small_geometry_filter': False,
            'geometry_simplified': False, 'display': 'on-demand native viewport',
            'svg_available': bool(self.render_dir),
            'minimum_feature_um': minimum, 'minimum_view_span_um': minimum * 4,
            'feature_method': 'path width, rectangle short side, polygon 2A/P; instance scaling included',
            'fit_margin': 1.04, 'lod_feature_pixels': 1.5,
            'density_grid_side': DENSITY_SIDE, 'density_measure': 'overlap-weighted polygon area',
            'background': '#101827', 'placement_grid': False,
        }

    def view_mode(self, box, width, selected, mode, ratio):
        if mode != 'auto':
            return mode
        features = [self.scales[key]['typical_um'] for key in selected if key in self.scales]
        feature = float(np.median(features)) if features else self.metadata['minimum_feature_um']
        css_pixel_um = (box[2] - box[0]) / width * ratio
        return 'density' if feature / css_pixel_um < self.metadata['lod_feature_pixels'] else 'geometry'

    def render(self, query):
        box, width, height, selected = parse_view(query, self.layers)
        options = parse_qs(query)
        mode = self.view_mode(box, width, selected, options.get('mode', ['auto'])[0],
                              float(options.get('pixel_ratio', ['1'])[0]))
        self.last_mode = mode
        keys = sorted(selected)
        typical = float(np.median([x['typical_um'] for x in self.scales.values()])) if self.scales else 1
        def priority(key):
            if len(keys) == 1:
                return 1.0
            info = self.layer_metadata[key]
            broad = info['feature'] and info['feature']['typical_um'] > typical * 6
            return .12 if broad or 'boundary' in info['name'].lower() else 1.0
        colors = [tuple(bytes.fromhex(self.layer_metadata[key]['color'][1:])) for key in keys]
        if mode == 'density':
            scale = min(1, DENSITY_SIDE / width, DENSITY_SIDE / height)
            nx, ny = max(1, round(width * scale)), max(1, round(height * scale))
            cache_key = (box, nx, ny)
            if cache_key != self.density_cache_key:
                self.density_cache_key, self.density_cache = cache_key, {}
            def weights():
                for key in keys:
                    if key not in self.density_cache:
                        self.density_cache[key] = coverage_grid(
                            self.db, self.top, self.layer_indices[key], box, self.layout.dbu, nx, ny)
                    # Smooth logarithmic/sqrt transfer reveals sparse wires;
                    # finite area contributes even when its width is subpixel.
                    yield priority(key) * 1.8 * np.sqrt(np.log1p(self.density_cache[key]))
            pixels = composite(weights(), colors, (ny, nx))
            image = Image.fromarray(pixels).resize((width, height), Image.Resampling.NEAREST)
        else:
            def weights():
                for layer in self.layers.values():
                    layer.visible = False
                for key in keys:
                    layer = self.layers[key]
                    layer.visible = True
                    native = self.view.get_pixels_with_options(
                        width, height, 1, 1, 0, self.db.DBox(*box))
                    with Image.open(BytesIO(bytes(native.to_png_data()))) as mask:
                        yield priority(key) * np.asarray(mask.convert('L'), dtype=np.float32) / 255
                    layer.visible = False
            image = Image.fromarray(composite(weights(), colors, (height, width)))
        output = BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()


def make_handler(renderer):
    class Handler(BaseHTTPRequestHandler):
        def send_bytes(self, data, content_type, status=200, headers=None):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            # Bind only to loopback and reject DNS-rebinding hosts. No CORS.
            host = self.headers.get('Host', '')
            if host not in {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}:
                self.send_error(403, 'Local host required')
                return
            route = urlsplit(self.path)
            try:
                if route.path in ('/', '/index.html'):
                    self.send_bytes(viewer_page(), 'text/html; charset=utf-8')
                elif route.path == '/api/metadata':
                    self.send_bytes(json.dumps(renderer.metadata).encode(), 'application/json')
                elif route.path == '/api/render':
                    data = renderer.render(route.query)
                    self.send_bytes(data, 'image/png', headers={'X-View-Mode': renderer.last_mode})
                elif route.path in ('/layout.gds', '/layout.svg', '/render-manifest.json'):
                    path = renderer.source if route.path == '/layout.gds' else (
                        renderer.render_dir / route.path[1:] if renderer.render_dir else None)
                    if path is None or not path.is_file():
                        self.send_error(404)
                        return
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/octet-stream')
                    self.send_header('Content-Disposition', f'attachment; filename="{route.path[1:]}"')
                    self.send_header('Content-Length', str(path.stat().st_size))
                    self.end_headers()
                    with path.open('rb') as stream:
                        shutil.copyfileobj(stream, self.wfile)
                else:
                    self.send_error(404)
            except (ValueError, OverflowError) as error:
                self.send_bytes(json.dumps({'error': str(error)}).encode(), 'application/json', 400)
            except (BrokenPipeError, ConnectionResetError):
                pass  # A viewer tab was closed while the synchronous render finished.
            except Exception as error:
                self.log_error('render failed: %s', error)
                self.send_bytes(b'{"error":"Rendering failed; see the local service log"}', 'application/json', 500)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('gds', type=Path)
    parser.add_argument('--top')
    parser.add_argument('--render-dir', type=Path, help='Optional matching full-vector export bundle')
    parser.add_argument('--layer-properties', type=Path, help='Optional KLayout .lyp layer names')
    parser.add_argument('--port', type=int, default=8792)
    args = parser.parse_args()
    try:
        renderer = LayoutRenderer(args.gds, args.top, args.render_dir, args.layer_properties)
        # A single server/render thread bounds native work and keeps LayoutView
        # on the thread that created it. The browser coalesces view requests.
        with HTTPServer(('127.0.0.1', args.port), make_handler(renderer)) as server:
            print(f'Full-detail viewer: http://127.0.0.1:{server.server_port}', flush=True)
            print(f'GDS SHA-256: {renderer.metadata["source_sha256"]}', flush=True)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError, ImportError) as error:
        parser.exit(1, f'Viewer: {error}\nInstall requirements-viewer.txt for the native renderer.\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
