"""Export bounded static tiles without simplifying or dropping GDS polygons."""
from collections import Counter, OrderedDict
import gzip
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile

import gdstk
import klayout.db as db

from tools.render_gds import inventory

PALETTE = ['#60a5fa', '#fb923c', '#4ade80', '#f472b6', '#facc15', '#a78bfa', '#22d3ee']


def checksum(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def tile_range(box, bounds, columns, rows):
    """Conservatively include boundary-touching tiles; clamp to the layout."""
    result = []
    for low, high, start, stop, count in (
        (box[0], box[2], bounds[0], bounds[2], columns),
        (box[1], box[3], bounds[1], bounds[3], rows),
    ):
        first = max(0, min(count - 1, math.floor((low - start) / (stop - start) * count)))
        last = max(0, min(count - 1, math.floor((high - start) / (stop - start) * count)))
        result.append(range(first, last + 1))
    return result


def export(source, destination, expected_sha256, columns=32, rows=16, previews=True):
    source, destination = Path(source), Path(destination)
    if checksum(source) != expected_sha256:
        raise ValueError('selected GDS checksum differs')
    if not (1 <= columns <= 64 and 1 <= rows <= 64):
        raise ValueError('tile dimensions must be 1..64')
    lib = gdstk.read_gds(source)
    if len(lib.top_level()) != 1:
        raise ValueError('expected exactly one top cell')
    expected = inventory(lib.top_level()[0])
    top_name = lib.top_level()[0].name
    del lib
    layout = db.Layout(); layout.read(str(source)); top = layout.cell(top_name)
    bounds_box = top.dbbox()
    bounds = [bounds_box.left, bounds_box.bottom, bounds_box.right, bounds_box.top]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError('empty layout bounds')
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'tiles').mkdir()
    layers = []
    actual = Counter()
    texts = 0
    with tempfile.TemporaryDirectory(prefix='gds-tiles-') as temporary:
        temp = Path(temporary); handles = OrderedDict()
        try:
            for index in layout.layer_indexes():
                info = layout.get_info(index); key = (info.layer, info.datatype)
                if key not in expected:
                    continue
                layer = len(layers)
                layers.append({'key': f'{key[0]}/{key[1]}', 'color': PALETTE[layer % len(PALETTE)],
                               'polygons': expected[key], 'preview': f'layer-{layer}.png'})
                iterator = top.begin_shapes_rec(index)
                while not iterator.at_end():
                    shape = iterator.shape()
                    if shape.is_text():
                        texts += 1
                    elif shape.is_box() or shape.is_polygon() or shape.is_path() or shape.is_simple_polygon():
                        polygon = shape.dpolygon.transformed(iterator.dtrans())
                        points = [[[p.x, p.y] for p in polygon.each_point_hull()]]
                        points.extend([[p.x, p.y] for p in polygon.each_point_hole(h)] for h in range(polygon.holes()))
                        box = polygon.bbox()
                        encoded = (json.dumps([layer, points], separators=(',', ':')) + '\n').encode()
                        xs, ys = tile_range([box.left, box.bottom, box.right, box.top], bounds, columns, rows)
                        for y in ys:
                            for x in xs:
                                name = f'{x}-{y}'
                                handle = handles.pop(name, None)
                                if handle is None:
                                    if len(handles) >= 48:
                                        _, old = handles.popitem(last=False); old.close()
                                    handle = (temp / name).open('ab')
                                handles[name] = handle
                                handle.write(encoded)
                        actual[key] += 1
                    iterator.next()
                print(f'Layer {key}: {actual[key]} polygons', flush=True)
        finally:
            for handle in handles.values(): handle.close()
        if actual != expected:
            raise ValueError('independent KLayout and gdstk polygon inventories differ')
        tiles = {}
        for path in sorted(temp.iterdir()):
            target = destination / 'tiles' / (path.name + '.jsonl.gz')
            with path.open('rb') as incoming, target.open('wb') as raw:
                with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            tiles[path.name] = {'sha256': checksum(target), 'bytes': target.stat().st_size}
    if previews:
        import klayout.lay as lay
        view = lay.LayoutView(); view.load_layout(str(source), False)
        view.min_hier_levels = 0; view.max_hier_levels = top.hierarchy_levels() + 1
        for k, v in {'abstract-mode-enabled':'false', 'drop-small-cells':'false',
                     'grid-visible':'false', 'background-color':'#000000', 'drawing-workers':'1'}.items():
            view.set_config(k, v)
        view.add_missing_layers()
        native = list(view.each_layer())
        for item in native: item.visible = False
        width = 1024; height = max(1, round(width * bounds_box.height() / bounds_box.width()))
        for layer in layers:
            selected = [item for item in native if f'{item.source_layer}/{item.source_datatype}' == layer['key']]
            for item in selected:
                item.visible = True; item.dither_pattern = 0
                item.fill_color = int(layer['color'][1:], 16); item.frame_color = item.fill_color
            pixels = view.get_pixels_with_options(width, height, 1, 1, 0, bounds_box)
            (destination / layer['preview']).write_bytes(bytes(pixels.to_png_data()))
            for item in selected: item.visible = False
    if checksum(source) != expected_sha256:
        raise ValueError('GDS changed during export')
    shutil.copy2(source, destination / 'layout.gds')
    template = Path(__file__).parent / 'static_gds_viewer'
    for name in ['index.html', 'viewer.js', 'viewer.css']:
        shutil.copy2(template / name, destination / name)
    report = {'schema':1, 'top':top_name, 'source_sha256':expected_sha256, 'bounds':bounds,
              'dbu_um':layout.dbu, 'columns':columns, 'rows':rows, 'layers':layers, 'tiles':tiles,
              'expanded_polygons':sum(actual.values()), 'independent_inventory_matches':True,
              'geometry_simplified':False, 'hierarchy_depth_limit':None, 'layers_omitted':[],
              'overview':'raster preview; subpixel features may not be visible',
              'detail':'original transformed polygon coordinates; duplicated at tile boundaries',
              'text_labels':'not drawn; original GDS retains labels', 'gdstk_version':gdstk.__version__,
              'klayout_version':db.__version__}
    (destination / 'manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    return report
