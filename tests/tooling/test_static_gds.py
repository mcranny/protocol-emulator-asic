"""Static publication retains geometry and rejects unbound layout inputs."""
import gzip
import hashlib
import json
import math
import tarfile

import gdstk
import pytest

from tools.build_pages import extract_layout
from tools.export_gds_static import export, tile_range


@pytest.fixture
def layout(tmp_path):
    lib = gdstk.Library(unit=1e-6, precision=1e-9)
    leaf = lib.new_cell('tiny')
    leaf.add(gdstk.rectangle((0, 0), (.001, .002), layer=19, datatype=7))
    leaf.add(gdstk.FlexPath([(0, 0), (1, 0)], .01, simple_path=True, layer=8))
    top = lib.new_cell('top')
    top.add(gdstk.Reference(leaf, origin=(2, 3), rotation=math.pi/2,
                           x_reflection=True, columns=2, rows=2, spacing=(2, 2)))
    top.add(gdstk.rectangle((0, 0), (8, 8), layer=999))
    source = tmp_path/'tiny.gds'; lib.write_gds(source)
    return source, top


def test_export_keeps_transforms_arrays_paths_and_tiny_geometry(layout, tmp_path):
    source, top = layout
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    report = export(source, tmp_path/'site', digest, columns=2, rows=2, previews=True)
    assert report['expanded_polygons'] == 9
    assert report['geometry_simplified'] is False and report['layers_omitted'] == []
    assert report['independent_inventory_matches'] is True
    assert all((tmp_path/'site'/layer['preview']).is_file() for layer in report['layers'])
    actual = set()
    for tile in (tmp_path/'site/tiles').glob('*.gz'):
        assert hashlib.sha256(tile.read_bytes()).hexdigest() == report['tiles'][tile.name.removesuffix('.jsonl.gz')]['sha256']
        for line in gzip.decompress(tile.read_bytes()).decode().splitlines():
            layer, rings = json.loads(line)
            assert len(rings) == 1
            points = rings[0]
            actual.add((report['layers'][layer]['key'], frozenset((round(x, 9),round(y, 9)) for x,y in points)))
    expected = {(f'{p.layer}/{p.datatype}', frozenset((round(x, 9),round(y, 9)) for x,y in p.points)) for p in top.get_polygons()}
    assert actual == expected


@pytest.mark.parametrize('box,expected', [((-1,-1,0,0),([0],[0])),((3,3,5,5),([0,1],[0,1])),((8,8,9,9),([1],[1]))], ids=['lower-bound','cross-boundary','upper-bound'])
def test_tile_bounds_include_crossing_geometry(box, expected):
    assert tuple(map(list,tile_range(box,[0,0,8,8],2,2))) == expected


def test_export_rejects_wrong_source(layout, tmp_path):
    source,_ = layout
    with pytest.raises(ValueError, match='checksum'):
        export(source,tmp_path/'site','0'*64)
    assert not (tmp_path/'site').exists()


@pytest.mark.parametrize('failure', ['archive-hash','gds-hash','duplicate','link'], ids=['archive-hash','gds-hash','duplicate-member','symlink-member'])
def test_archive_binding_rejects_invalid_input(layout, tmp_path, failure):
    source,_ = layout
    archive=tmp_path/'layout.tar.gz'
    with tarfile.open(archive,'w:gz') as tar:
        if failure=='link':
            member=tarfile.TarInfo('layout.gds');member.type=tarfile.SYMTYPE;member.linkname='/etc/passwd';tar.addfile(member)
        else:
            tar.add(source,arcname='layout.gds')
            if failure=='duplicate':tar.add(source,arcname='layout.gds')
    spec={'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'gds_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'gds_member':'layout.gds'}
    if failure=='archive-hash':spec['archive_sha256']='0'*64
    if failure=='gds-hash':spec['gds_sha256']='0'*64
    with pytest.raises(ValueError):extract_layout(archive,tmp_path/'out.gds',spec)
