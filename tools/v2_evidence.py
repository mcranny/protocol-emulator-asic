"""Record local signoff stages and assemble a checked V2 acceptance bundle.

Run `record` around the actual command, with new output artifact paths. The
command must produce every declared artifact. GDS/netlist inputs are hashed
before and after execution. `assemble` validates the resulting bundle.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

try:
    from .check_v2 import accept, checksum, verify_files
    from .validate_local import source_state
except ImportError:
    from check_v2 import accept, checksum, verify_files
    from validate_local import source_state


def read(path):
    return json.loads(Path(path).read_text())


def record(destination, kind, artifacts, command, gds=None, netlist=None):
    before = source_state()
    if before['source_dirty']:
        raise ValueError('signoff requires a clean checkout')
    if not command or any(path.exists() for path in artifacts.values()):
        raise ValueError('a command and new artifact paths are required')
    required = {'precheck': 'precheck.xml', 'gatelevel': 'gatelevel.xml',
                'render': 'render-manifest.json'}[kind]
    if required not in artifacts or (kind in ('precheck', 'render') and gds is None) or (kind == 'gatelevel' and netlist is None):
        raise ValueError('missing stage input or output')
    inputs = {name: Path(path) for name, path in (('gds', gds), ('netlist', netlist)) if path is not None}
    input_hashes = {name + '_sha256': checksum(path) for name, path in inputs.items()}
    destination.mkdir(parents=True, exist_ok=False)
    root = destination / 'artifacts'
    root.mkdir()
    manifest = dict(before, **input_hashes, schema=1, stage=kind, command=command, exit_code=None)
    def save():
        (destination / 'stage.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    save()
    try:
        with (root / 'command.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        manifest['exit_code'] = result.returncode
        if result.returncode:
            raise ValueError(f'{kind} command failed: {result.returncode}')
        if source_state() != before or input_hashes != {name + '_sha256': checksum(path) for name, path in inputs.items()}:
            raise ValueError('stage source or physical input changed during execution')
        for name, path in artifacts.items():
            if Path(name).name != name:
                raise ValueError('artifact names must be simple filenames')
            shutil.copy2(path, root / name)
        manifest['artifacts'] = {path.name: checksum(path) for path in root.iterdir()}
    except (ValueError, OSError) as error:
        manifest['exit_code'] = manifest['exit_code'] or 1
        manifest['error'] = str(error)
        raise
    finally:
        save()


def continuation_inputs(physical, identity):
    """Verify a clean, hash-bound checkpoint continuation before bundling it."""
    manifest = read(physical / 'manifest.json')
    status = read(physical / 'route-status.json')
    if identity['source_dirty'] or any(manifest.get(k) != v for k, v in identity.items()):
        raise ValueError('physical continuation differs from clean functional source')
    if status.get('phase') != 'finished' or status.get('passed') is not True or status.get('issues'):
        raise ValueError('physical continuation is incomplete or failed')
    verify_files(physical, manifest['prepared_sha256'])
    inventory = read(physical / 'physical-artifacts.json')
    verify_files(physical, inventory)
    for name, digest in manifest['prepared_sha256'].items():
        source = name if name.startswith('src/') else 'tools/' + name
        if source in identity['inputs'] and identity['inputs'][source] != digest:
            raise ValueError(f'physical prepared source mismatch: {source}')
    final = physical / 'runs/signoff/final'
    metrics = read(physical / 'acceptance-metrics.json')
    raw = read(final / 'metrics.json')
    audit = read(physical / 'mapping-audit.json')
    provenance = read(physical / 'metrics-provenance.json')
    if (provenance.get('raw_metrics_sha256') != checksum(final / 'metrics.json')
            or provenance.get('mapping_audit_sha256') != checksum(physical / 'mapping-audit.json')
            or audit.get('passed') is not True or audit.get('unmapped_count') != 0
            or audit.get('netlist_sha256') != checksum(final / 'nl/tt_um_mcranny_protocol_emulator_v2.nl.v')
            or metrics != dict(raw, design__instance_unmapped__count=audit['unmapped_count'])):
        raise ValueError('physical metric derivation is missing or changed')
    return final, physical / 'acceptance-metrics.json', physical / 'runs/signoff/resolved.json'


def assemble(destination, functional, physical, stages):
    function = read(functional / 'stage.json')
    identity = {key: function[key] for key in ('source_commit', 'source_dirty', 'inputs')}
    if identity['source_dirty'] or function.get('exit_code') != 0:
        raise ValueError('functional evidence is dirty or failed')
    continuation = (physical / 'physical-artifacts.json').is_file()
    if continuation:
        final, metric_path, config_path = continuation_inputs(physical, identity)
    else:
        candidate = read(physical / 'candidate-inputs.json')
        build = read(physical / 'local-evidence.json')
        if any(candidate.get(key) != value for key, value in identity.items()):
            raise ValueError('physical candidate differs from the functional source')
        if (build.get('source_commit') != identity['source_commit'] or build.get('source_dirty') is not False
                or build.get('exit_code') != 0 or build.get('stage') != 'full'):
            raise ValueError('physical build is incomplete, stale or dirty')
        verify_files(physical, build['files'])
        verify_files(physical, candidate['prepared'])
        final = physical / 'src/runs/local/final'
        metric_path, config_path = final / 'metrics.json', final.parent / 'resolved.json'
    verify_files(functional / 'source', identity['inputs'])
    verify_files(functional / 'artifacts', function['artifacts'])
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(functional / 'source', destination / 'source')
    root = destination / 'artifacts'
    shutil.copytree(functional / 'artifacts', root)
    records = {'functional': function, 'physical': dict(identity, exit_code=0)}
    # Record the pinned tool/PDK manifests alongside the source and reports.
    if continuation:
        for name in ('manifest.json', 'predecessor.json', 'physical-artifacts.json',
                     'electrical-gate.json', 'diode-connectivity.json', 'mapping-audit.json',
                     'metrics-provenance.json', 'route-status.json'):
            shutil.copy2(physical / name, root / ('physical-' + name))
    else:
        shutil.copy2(physical / 'local-evidence.json', root / 'physical-build.json')
        shutil.copy2(physical / 'candidate-inputs.json', root / 'physical-inputs.json')
    top = 'tt_um_mcranny_protocol_emulator_v2'
    for name, path in {'metrics.json': metric_path,
                       'config.json': config_path,
                       'netlist.v': final / 'nl' / (top + '.nl.v'),
                       'layout.gds': final / 'gds' / (top + '.gds')}.items():
        shutil.copy2(path, root / name)
    for kind, directory in stages.items():
        stage = read(directory / 'stage.json')
        if (stage.get('stage') != kind or stage.get('exit_code') != 0
                or any(stage.get(key) != value for key, value in identity.items())):
            raise ValueError(f'{kind} stage is failed, stale or dirty')
        verify_files(directory / 'artifacts', stage['artifacts'])
        for name in stage['artifacts']:
            target = root / name
            if name == 'command.log':
                target = root / f'{kind}-command.log'
            elif target.exists():
                raise ValueError(f'duplicate artifact: {name}')
            shutil.copy2(directory / 'artifacts' / name, target)
        records[kind] = stage
    manifest = dict(schema=2, source_commit=identity['source_commit'], inputs=identity['inputs'],
                    artifacts={path.name: checksum(path) for path in root.iterdir()}, stages=records)
    (destination / 'evidence.json').write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')
    result = accept(destination)
    (destination / 'acceptance.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='mode', required=True)
    stage = commands.add_parser('record')
    stage.add_argument('--kind', choices=('precheck', 'gatelevel', 'render'), required=True)
    stage.add_argument('--output', type=Path, required=True)
    stage.add_argument('--artifact', action='append', default=[], metavar='NAME=PATH')
    stage.add_argument('--gds', type=Path)
    stage.add_argument('--netlist', type=Path)
    stage.add_argument('command', nargs=argparse.REMAINDER)
    bundle = commands.add_parser('assemble')
    for name in ('output', 'functional', 'physical', 'precheck', 'gatelevel', 'render'):
        bundle.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.mode == 'record':
            artifacts = dict(entry.split('=', 1) for entry in args.artifact)
            if len(artifacts) != len(args.artifact):
                raise ValueError('duplicate artifact names')
            command = args.command[1:] if args.command[:1] == ['--'] else args.command
            record(args.output, args.kind, {name: Path(path) for name, path in artifacts.items()},
                   command, args.gds, args.netlist)
        else:
            print(json.dumps(assemble(args.output, args.functional, args.physical,
                                      {kind: getattr(args, kind) for kind in ('precheck', 'gatelevel', 'render')}), indent=2))
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f'V2 evidence failed: {error}\n')
