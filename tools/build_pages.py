"""Build strict Sphinx HTML and the checksum-bound, static V2 layout viewer."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

from tools.export_gds_static import checksum, export

ROOT = Path(__file__).resolve().parents[1]


def extract_layout(archive, destination, spec):
    if checksum(archive) != spec['archive_sha256']:
        raise ValueError('layout archive checksum differs')
    with tarfile.open(archive, 'r:gz') as contents:
        members = [m for m in contents.getmembers() if m.name == spec['gds_member']]
        if len(members) != 1 or not members[0].isfile():
            raise ValueError('missing, duplicate or nonregular GDS member')
        with contents.extractfile(members[0]) as incoming, Path(destination).open('wb') as outgoing:
            shutil.copyfileobj(incoming, outgoing)
    if checksum(destination) != spec['gds_sha256']:
        raise ValueError('extracted GDS checksum differs')


def build(output, archive=None):
    output = Path(output).resolve()
    if output.exists(): raise ValueError('output must be a new directory')
    spec = json.loads((ROOT/'docs/layout-source.json').read_text())
    subprocess.run([sys.executable, '-m', 'sphinx', '-n', '-W', '--keep-going', '-b', 'html',
                    str(ROOT/'docs'), str(output)], check=True)
    with tempfile.TemporaryDirectory(prefix='pages-layout-') as temporary:
        temp = Path(temporary)
        if archive is None:
            archive = temp/'layout.tar.gz'
            with urlopen(spec['archive_url'], timeout=120) as response, archive.open('wb') as stream:
                shutil.copyfileobj(response, stream)
        gds = temp/'layout.gds'
        extract_layout(archive, gds, spec)
        report = export(gds, output/'layout', spec['gds_sha256'])
    report['provenance'] = spec
    (output/'layout/manifest.json').write_text(json.dumps(report, indent=2)+'\n')
    (output/'.nojekyll').touch()
    size = sum(p.stat().st_size for p in output.rglob('*') if p.is_file())
    if size >= 900_000_000: raise ValueError('site exceeds the conservative Pages size budget')
    print(json.dumps({'site_bytes':size, 'expanded_polygons':report['expanded_polygons']}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--archive', type=Path, help='Optional local copy of the pinned release archive')
    args = parser.parse_args()
    build(args.output, args.archive)
