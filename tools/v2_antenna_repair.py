"""Prepare a retained diode-only antenna repair from a verified routed V2 ODB."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

try:
    from . import v2_closure as closure
    from .v2_route_trial import extracted_issues
    from .validate_local import source_state
except ImportError:
    import v2_closure as closure
    from v2_route_trial import extracted_issues
    from validate_local import source_state

ROOT = Path(__file__).resolve().parents[1]


def relocate(value):
    if isinstance(value, str) and value.startswith('/closure/'):
        return '/routed/' + value[len('/closure/'):]
    if isinstance(value, dict):
        return {k: relocate(v) for k, v in value.items()}
    if isinstance(value, list):
        return [relocate(v) for v in value]
    return value


def repair_config(config):
    for key, expected in closure.FIXED.items():
        if key not in ('OPENROAD_THREADS', 'STA_THREADS') and config.get(key) != expected:
            raise ValueError(f'fixed physical constraint differs: {key}')
    return dict(config, DESIGN_DIR='/closure', OPENROAD_THREADS=6, STA_THREADS=6,
                DRT_ANTENNA_REPAIR_ITERS=6, DRT_ANTENNA_REPAIR_JUMPER_ONLY=False,
                DRT_ANTENNA_REPAIR_DIODE_ONLY=True, DRT_SAVE_SNAPSHOTS=True,
                DRT_SAVE_DRC_REPORT_ITERS=1)


def prepare(args):
    previous, destination = args.previous.resolve(), args.output.resolve()
    old = closure.read(previous / 'manifest.json')
    original = Path(old['resume_directory'])
    pdk, tt = ROOT / 'build/deps/v2-pdk', ROOT / 'build/deps/v2-tt'
    closure.verify_checkout(pdk, closure.PDK_REV)
    closure.verify_checkout(tt, closure.TT_REV)
    for key, expected in (('image', closure.IMAGE), ('pdk_commit', closure.PDK_REV),
                          ('support_tools_commit', closure.TT_REV)):
        if old.get(key) != expected:
            raise ValueError(f'predecessor dependency changed: {key}')
    for name, digest in old['prepared_sha256'].items():
        if closure.sha(previous / name) != digest:
            raise ValueError(f'predecessor prepared input changed: {name}')
    for name in closure.RTL:
        if closure.sha(ROOT / 'src' / name) != closure.sha(previous / 'src' / name):
            raise ValueError(f'RTL differs from routed candidate: {name}')
    metrics = closure.read(previous / 'runs/route/final/metrics.json')
    allowed = ['missing/invalid/nonzero routed metric: route__antenna_violation__count']
    if extracted_issues(metrics) != allowed or metrics['route__drc_errors'] != 0:
        raise ValueError('predecessor must have clean DRT and extracted timing with antenna as its only failing gate')
    if not isinstance(metrics.get('route__antenna_violation__count'), int) or metrics['route__antenna_violation__count'] <= 0:
        raise ValueError('expected measured nonzero antenna violations')
    state_file = previous / 'runs/route/03-openroad-checkantennas-1/state_out.json'
    state = dict(closure.read(state_file), metrics={})
    config = repair_config(closure.read(previous / 'runs/route/resolved.json'))
    if closure.output('docker', 'ps', '--format', '{{.Names}}'):
        raise ValueError('another container is running')
    info = json.loads(closure.output('docker', 'info', '--format', '{{json .}}'))
    if info['NCPU'] < 6 or info['MemTotal'] < 7 * 1024**3 or shutil.disk_usage(ROOT).free < 20 * 1024**3:
        raise ValueError('insufficient resources')
    mounts = {'/closure/tt': tt, '/closure': previous, '/previous': original, '/pdk': pdk}
    artifacts = {relocate(p): closure.sha(closure.mapped_path(p, mounts))
                 for p in closure.absolute_paths(state)}
    # Bind the gate reports and checkpoint to the read-only predecessor mount.
    for path in [state_file, previous / 'runs/route/final/metrics.json',
                 previous / 'runs/route/01-openroad-detailedrouting/or_metrics_out.json',
                 previous / 'runs/route/03-openroad-checkantennas-1/or_metrics_out.json']:
        artifacts['/routed/' + str(path.relative_to(previous))] = closure.sha(path)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(previous / 'src', destination / 'src')
    for name in ('v2_fresh_route.py', 'v2_route_flow.py', 'v2_route_trial.py', 'v2_route_monitor.py',
                 'v2_closure.py', 'local_flow.py', 'v2_antenna_connectivity.py', 'v2_antenna_repair.py'):
        shutil.copy2(ROOT / 'tools' / name, destination / name)
    closure.write(destination / 'resolved.json', config)
    closure.write(destination / 'state.json', relocate(state))
    closure.write(destination / 'baseline-metrics.json', metrics)
    command = ['docker', 'run', '--detach', '--name', args.name, '--network', 'none',
               '-v', f'{destination}:/closure', '-v', f'{previous}:/routed:ro',
               '-v', f'{original}:/previous:ro', '-v', f'{pdk}:/pdk:ro',
               '-v', f'{tt}:/closure/tt:ro', '-w', '/closure', closure.IMAGE,
               'python', '/closure/v2_fresh_route.py', '--inside']
    manifest = dict(schema=1, release_acceptance=False, **source_state(ROOT),
                    image=closure.IMAGE, pdk_commit=closure.PDK_REV, support_tools_commit=closure.TT_REV,
                    container_name=args.name, threads=6, antenna_checkpoint=True,
                    repair_mode='diode-only antenna repair from clean routing',
                    baseline_antenna_nets=metrics['route__antenna_violation__count'],
                    routing_search=dict(seed=43, swaps_per_net=1), max_runtime_hours=12,
                    docker_memory_bytes=info['MemTotal'], memory_stop_bytes=info['MemTotal']-1024**3,
                    command=command, resume_artifact_sha256=artifacts,
                    prepared_sha256={str(p.relative_to(destination)): closure.sha(p)
                        for p in destination.rglob('*') if p.is_file()})
    closure.write(destination / 'manifest.json', manifest)
    container = closure.output(*command)
    closure.write(destination / 'launch.json', dict(container_id=container))
    with (destination / 'sleep-guard.log').open('w') as log:
        guard = subprocess.Popen(['caffeinate', '-i', 'docker', 'wait', args.name],
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    closure.write(destination / 'sleep-guard.json', dict(pid=guard.pid, container=args.name))
    print(container)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--name', required=True)
    prepare(parser.parse_args())
