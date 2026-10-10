"""Geometry-derived scales and exact area aggregation for the layout viewer."""
from collections import Counter
from functools import lru_cache
import math
import re
import xml.etree.ElementTree as ET

import numpy as np

BACKGROUND = (16, 24, 39)
DENSITY_SIDE = 200


def layer_names(path):
    """Read exact GDS layer/datatype labels from an optional KLayout .lyp file."""
    if path is None:
        return {}
    names = {}
    for node in ET.parse(path).iter():
        source, name = node.findtext('source', ''), node.findtext('name', '')
        match = re.fullmatch(r'(\d+)/(\d+)(?:@\d+)?', source.strip())
        if match and name:
            names[f'{int(match[1])}/{int(match[2])}'] = name
    return names


def feature_scales(layout, top):
    """Measure reachable shapes once per cell; account for instance scaling.

    Path width and rectangle short side are exact. For other polygons, 2A/P
    supplies a conservative characteristic width, not a DRC minimum-width rule.
    Recursive counters retain array multiplicity without expanding instances.
    """
    @lru_cache(None)
    def cell_scales(index):
        cell = layout.cell(index)
        result = {}
        for layer_index in layout.layer_indexes():
            histogram = Counter()
            for shape in cell.each_shape(layer_index):
                if shape.is_path():
                    value = abs(shape.path_width)
                elif shape.is_box():
                    value = min(shape.box_width, shape.box_height)
                elif shape.is_polygon() or shape.is_simple_polygon():
                    polygon = shape.polygon
                    perimeter = polygon.perimeter()
                    value = 2 * polygon.area() / perimeter if perimeter else 0
                else:
                    continue
                if value > 0:
                    histogram[round(value, 6)] += 1
            if histogram:
                result[layer_index] = histogram
        for instance in cell.each_inst():
            scale = abs(instance.cplx_trans.mag)
            copies = max(1, instance.na) * max(1, instance.nb)
            for layer_index, histogram in cell_scales(instance.cell_index).items():
                target = result.setdefault(layer_index, Counter())
                for width, count in histogram.items():
                    target[round(width * scale, 6)] += count * copies
        return result

    scales = {}
    for index, histogram in cell_scales(top.cell_index()).items():
        halfway, cumulative = sum(histogram.values()) / 2, 0
        typical = min(histogram)
        for width, count in sorted(histogram.items()):
            cumulative += count
            if cumulative >= halfway:
                typical = width
                break
        info = layout.get_info(index)
        scales[f'{info.layer}/{info.datatype}'] = {
            'minimum_um': max(layout.dbu, min(histogram) * layout.dbu),
            'typical_um': max(layout.dbu, typical * layout.dbu),
        }
    cell_scales.cache_clear()
    return scales


def coverage_grid(db, top, layer_index, box, dbu, nx, ny):
    """Per-bucket summed polygon area / bucket area, including subpixel shapes.

    Overlaps count repeatedly, intentionally. This is an overlap-weighted area
    density, not a union coverage percentage or a congestion/DRC metric. Only
    intersecting hierarchy is materialized, one layer at a time. Bucket edges
    snap to the GDS database grid (at most one dbu per bucket step).
    """
    origin = db.Point(math.floor(box[0] / dbu), math.floor(box[1] / dbu))
    step = db.Vector(max(1, math.ceil((box[2] - box[0]) / dbu / nx)),
                     max(1, math.ceil((box[3] - box[1]) / dbu / ny)))
    bounds = db.Box(origin, origin + db.Vector(step.x * nx, step.y * ny))
    region = db.Region(top.begin_shapes_rec_overlapping(layer_index, bounds))
    # KLayout returns rows from bottom to top; image scanlines run top to bottom.
    area = np.asarray(region.rasterize(origin, step, nx, ny), dtype=np.float32)
    return np.flipud(area) / (step.x * step.y)


def composite(weights, colors, shape):
    """Order-independent translucent blending avoids whitening dense overlaps."""
    total = np.zeros(shape, dtype=np.float32)
    rgb = np.zeros((*shape, 3), dtype=np.float32)
    for weight, color in zip(weights, colors):
        total += weight
        rgb += weight[..., None] * np.asarray(color, dtype=np.float32)
    alpha = -np.expm1(-total)
    average = rgb / np.maximum(total[..., None], 1e-10)
    background = np.asarray(BACKGROUND, dtype=np.float32)
    return np.clip(background + (average - background) * alpha[..., None], 0, 255).astype(np.uint8)
