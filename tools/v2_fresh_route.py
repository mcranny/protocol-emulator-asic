"""Retained from-RTL V2 trial, with fresh singleton-corner STA before DRT.

Runs on immutable prepared inputs, records phase and resources, and never treats
successful routing alone as release acceptance. The normal router gets its full
iteration budget: short count plateaus are observations, not stop conditions.
"""
import argparse
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

try:
    from . import v2_closure as closure
    from .local_flow import user_config
    from .v2_route_monitor import sample
    from .v2_route_trial import resource_reason, stop_child, extracted_issues
except ImportError:
    import v2_closure as closure
    from local_flow import user_config
    from v2_route_monitor import sample
    from v2_route_trial import resource_reason, stop_child, extracted_issues

ROOT = Path(__file__).resolve().parents[1]


def retained_state(value):
    """Relocate only container-local checkpoint views to the read-only mount."""
    if isinstance(value, str) and value.startswith('/closure/'):
        return '/previous/' + value[len('/closure/'):]
    if isinstance(value, dict):
        return {key: retained_state(item) for key, item in value.items()}
    if isinstance(value, list):
        return [retained_state(item) for item in value]
    return value


def candidate_config(base, overrides, project, sizes, threads):
    config = dict(base, **overrides)
    config.update(user_config(project, sizes))
    config.update({key: value for key, value in closure.FIXED.items()
                   if key not in ('OPENROAD_THREADS', 'STA_THREADS')})
    config.update(OPENROAD_THREADS=threads, STA_THREADS=threads,
                  DRT_SAVE_SNAPSHOTS=True, DRT_SAVE_DRC_REPORT_ITERS=1)
    config['VERILOG_FILES'] = ['/closure/src/' + name for name in project['source_files']]
    config['FP_DEF_TEMPLATE'] = '/closure/tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def'
    if project['top_module'] != 'tt_um_mcranny_protocol_emulator_v2':
        raise ValueError('expected V2 integration top')
    return config


def audit(root):
    from librelane.flows import SequentialFlow
    from librelane.steps import Step
    from librelane.state import State

    class CornerTiming(SequentialFlow):
        Steps = [Step.factory.get('OpenROAD.STAMidPNR')]

    config, state = closure.read(root / 'resolved.json'), closure.read(root / 'state.json')
    summary = dict(passed=False, release_acceptance=False, corners={},
                   state_sha256=closure.sha(root / 'state.json'),
                   config_sha256=closure.sha(root / 'resolved.json'))
    for corner in closure.CORNERS:
        cfg = dict(config, PNR_CORNERS=[corner], STA_CORNERS=[corner], DEFAULT_CORNER=corner,
                   STA_EXTRA_CORNER_TCL_FILE='/closure/audit.tcl')
        cfg.pop('meta', None)
        directory = root / 'timing' / corner
        flow = CornerTiming(cfg, design_dir=str(root), pdk_root='/pdk', pdk='ihp-sg13cmos5l')
        flow.start(with_initial_state=State.load(state), _force_run_dir=str(directory))
        step = closure.timing_step(directory)
        metrics = closure.read(step / 'or_metrics_out.json')
        issues = closure.timing_issues(metrics, corner)
        subprocess.run(['openroad', '-exit', '-no_splash', '-python',
                        '/closure/v2_closure_annotation.py', state['odb'],
                        str(step / 'checks.rpt'), str(step / 'annotation.json')], check=True)
        annotation = closure.read(step / 'annotation.json')
        if not annotation['passed']:
            issues.append('parasitic annotation audit failed')
        summary['corners'][corner] = dict(issues=issues, metrics=metrics, annotation=annotation)
        closure.write(root / 'timing-gate.json', summary)
    summary['passed'] = all(not c['issues'] for c in summary['corners'].values())
    summary['artifact_sha256'] = {str(p.relative_to(root)): closure.sha(p)
        for p in (root / 'timing').rglob('*') if p.is_file()}
    closure.write(root / 'timing-gate.json', summary)
    return 0 if summary['passed'] else 2


def inside():
    root = Path('/closure')
    manifest = closure.read(root / 'manifest.json')
    for name, digest in manifest['prepared_sha256'].items():
        if closure.sha(root / name) != digest:
            raise ValueError(f'prepared input changed: {name}')
    for name, digest in manifest.get('resume_artifact_sha256', {}).items():
        if closure.sha(name) != digest:
            raise ValueError(f'retained checkpoint changed: {name}')
    status = dict(phase='running', stage='synthesis and placement', started_unix=time.time(),
                  passed=False, release_acceptance=False, stop_reason=None)

    def run(command, stage):
        status['stage'] = stage
        closure.write(root / 'route-status.json', status)
        with (root / 'flow.log').open('a') as log:
            log.write('\n=== ' + stage + ' ===\n')
            log.flush()
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while child.poll() is None:
                    resource, _ = sample(root)
                    reason = resource_reason(resource, manifest['memory_stop_bytes'])
                    if time.time() - status['started_unix'] > manifest['max_runtime_hours'] * 3600:
                        reason = reason or 'maximum trial runtime reached'
                    if reason:
                        status['stop_reason'] = reason
                        stop_child(child)
                        break
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
            except BaseException:
                if child.poll() is None:
                    stop_child(child)
                raise
        sample(root)
        if child.returncode:
            raise RuntimeError(f'{stage} exited {child.returncode}')

    common = ['--manual-pdk', '--pdk-root', '/pdk', '--pdk', 'ihp-sg13cmos5l',
              '-j', str(manifest['threads'])]
    def interrupted(signum, frame):
        raise InterruptedError(f'supervisor received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    try:
        if not manifest.get('antenna_checkpoint'):
            resume = (['--from', 'OpenROAD.ResizerTimingPostCTS', '--with-initial-state',
                       '/closure/resume-state.json'] if manifest.get('resume_directory') else [])
            run([sys.executable, '-m', 'librelane', *common, '--run-tag', 'pre-route',
                 *resume, '--to', 'OpenROAD.STAMidPNR-3', '/closure/config.json'],
                'post-CTS repair through global routing' if resume else 'synthesis through global routing')
            directory = root / 'runs/pre-route'
            checkpoints = list(directory.glob('*-openroad-stamidpnr-3/state_out.json'))
            if len(checkpoints) != 1:
                raise ValueError('expected exactly one post-global-route checkpoint')
            state = dict(closure.read(checkpoints[0]), metrics={})
            closure.write(root / 'state.json', state)
            config = closure.read(directory / 'resolved.json')
            closure.write(root / 'resolved.json', config)
            closure.write(root / 'checkpoint-sha256.json',
                          {p: closure.sha(p) for p in closure.absolute_paths(state) if Path(p).is_file()})
            run([sys.executable, '/closure/v2_fresh_route.py', '--audit'], 'fresh three-corner timing gate')
            if closure.read(root / 'timing-gate.json').get('passed') is not True:
                raise ValueError('fresh pre-route timing gate failed')
            run(['openroad', '-exit', '-no_splash', '-python', '/closure/v2_route_geometry.py',
                 state['odb'], '/closure/geometry.json'], 'recording placement geometry')
        run([sys.executable, '/closure/v2_route_flow.py', *common, '--run-tag', 'route',
             '--from', 'OpenROAD.DetailedRouting', '--to', 'OpenROAD.STAPostPNR',
             '--with-initial-state', '/closure/state.json', '/closure/resolved.json'], 'detailed routing and extraction')
        if manifest.get('antenna_checkpoint'):
            run(['openroad', '-exit', '-no_splash', '-python', '/closure/v2_antenna_connectivity.py',
                 '/closure/runs/route/final/odb/tt_um_mcranny_protocol_emulator_v2.odb',
                 '/closure/diode-connectivity.json'], 'checking diode connectivity')
        metrics = closure.read(root / 'runs/route/final/metrics.json')
        status['issues'] = extracted_issues(metrics)
        status['passed'] = not status['issues']
        if status['issues']:
            status['stop_reason'] = 'physical gate requires review; see issues'
    except BaseException as error:
        status['stop_reason'] = status['stop_reason'] or f'{type(error).__name__}: {error}'
        raise
    finally:
        status.update(phase='finished', finished_unix=time.time())
        closure.write(root / 'route-status.json', status)
        sample(root)
    return 0 if status['passed'] else 2


def prepare(args):
    try:
        from .validate_local import source_state
    except ImportError:
        from validate_local import source_state
    destination = args.output.resolve()
    qualification = closure.read(args.qualification)
    if qualification.get('passed') is not True:
        raise ValueError('functional qualification has not passed')
    for name in closure.RTL:
        if qualification['rtl_sha256'].get('src/' + name) != closure.sha(ROOT / 'src' / name):
            raise ValueError(f'RTL changed after functional qualification: {name}')
    for name, digest in qualification['artifacts_sha256'].items():
        if closure.sha(args.qualification.parent / name) != digest:
            raise ValueError(f'functional evidence changed: {name}')
    pdk, tt = ROOT / 'build/deps/v2-pdk', ROOT / 'build/deps/v2-tt'
    closure.verify_checkout(pdk, closure.PDK_REV)
    closure.verify_checkout(tt, closure.TT_REV)
    if closure.output('docker', 'ps', '--format', '{{.Names}}'):
        raise ValueError('another container is running')
    info = json.loads(closure.output('docker', 'info', '--format', '{{json .}}'))
    if info['NCPU'] < 6 or info['MemTotal'] < 7 * 1024**3 or shutil.disk_usage(ROOT).free < 20 * 1024**3:
        raise ValueError('insufficient resources')
    image = json.loads(closure.output('docker', 'image', 'inspect', closure.IMAGE))[0]
    # Use the pinned image's YAML parser without adding a host dependency.
    project, sizes = json.loads(closure.output(
        'docker', 'run', '--rm', '--network', 'none',
        '-v', f'{ROOT / "physical/v2/info.yaml"}:/info.yaml:ro',
        '-v', f'{tt / "tech/ihp-sg13cmos5l/tile_sizes.yaml"}:/sizes.yaml:ro',
        closure.IMAGE, 'python', '-c',
        'import json,yaml; print(json.dumps([yaml.safe_load(open("/info.yaml"))["project"], '
        'yaml.safe_load(open("/sizes.yaml"))]))'))
    config = candidate_config(closure.read(ROOT / 'src/config.json'),
                              closure.read(ROOT / 'physical/v2/overrides.json'), project, sizes, 6)
    resume_artifacts, resume_state = {}, None
    if args.resume:
        previous = args.resume.resolve()
        if closure.read(previous / 'config.json') != config:
            raise ValueError('resume configuration differs from qualified candidate')
        for name in closure.RTL:
            if closure.sha(previous / 'src' / name) != closure.sha(ROOT / 'src' / name):
                raise ValueError(f'resume RTL mismatch: {name}')
        checkpoint = previous / 'runs/pre-route/36-openroad-stamidpnr-1/state_out.json'
        state = dict(closure.read(checkpoint), metrics={})
        resume_state = retained_state(state)
        for path in closure.absolute_paths(state):
            host = closure.mapped_path(path, {'/closure/tt': tt, '/closure': previous, '/pdk': pdk})
            if host.is_file():
                resume_artifacts[retained_state(path)] = closure.sha(host)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / 'src').mkdir()
    shutil.copy2(args.qualification, destination / 'qualification.json')
    for name in closure.RTL:
        shutil.copy2(ROOT / 'src' / name, destination / 'src' / name)
    shutil.copy2(ROOT / 'physical/v2/info.yaml', destination / 'info.yaml')
    for name in ('v2_fresh_route.py', 'v2_route_trial.py', 'v2_route_flow.py', 'v2_route_monitor.py',
                 'v2_closure.py', 'v2_closure_annotation.py', 'v2_route_geometry.py', 'local_flow.py'):
        shutil.copy2(ROOT / 'tools' / name, destination / name)
    shutil.copy2(ROOT / 'physical/v2/closure/audit.tcl', destination / 'audit.tcl')
    closure.write(destination / 'config.json', config)
    if resume_state is not None:
        closure.write(destination / 'resume-state.json', resume_state)
    command = ['docker', 'run', '--detach', '--name', args.name, '--network', 'none',
               '-v', f'{destination}:/closure', '-v', f'{pdk}:/pdk:ro',
               '-v', f'{tt}:/closure/tt:ro']
    if args.resume:
        command += ['-v', f'{args.resume.resolve()}:/previous:ro']
    command += ['-w', '/closure', closure.IMAGE, 'python', '/closure/v2_fresh_route.py', '--inside']
    manifest = dict(schema=1, release_acceptance=False, **source_state(ROOT),
                    image=closure.IMAGE, image_id=image['Id'], pdk_commit=closure.PDK_REV,
                    support_tools_commit=closure.TT_REV, container_name=args.name, threads=6,
                    routing_search=dict(seed=43, swaps_per_net=1), max_runtime_hours=24,
                    docker_memory_bytes=info['MemTotal'], docker_cpus=info['NCPU'],
                    memory_stop_bytes=info['MemTotal']-1024**3, command=command,
                    resume_directory=str(args.resume.resolve()) if args.resume else None,
                    resume_artifact_sha256=resume_artifacts,
                    prepared_sha256={str(p.relative_to(destination)): closure.sha(p)
                        for p in destination.rglob('*') if p.is_file()})
    closure.write(destination / 'manifest.json', manifest)
    container = closure.output(*command)
    closure.write(destination / 'launch.json', dict(container_id=container, launched_unix=time.time()))
    with (destination / 'sleep-guard.log').open('w') as log:
        guard = subprocess.Popen(['caffeinate', '-i', 'docker', 'wait', args.name], stdout=log,
                                 stderr=subprocess.STDOUT, start_new_session=True)
    closure.write(destination / 'sleep-guard.json', dict(pid=guard.pid, container=args.name))
    print(container)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--audit', action='store_true')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--name')
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--resume', type=Path, help='Resume an identical candidate from its complete post-CTS checkpoint')
    args = parser.parse_args()
    if args.audit:
        raise SystemExit(audit(Path('/closure')))
    if args.inside:
        raise SystemExit(inside())
    if not args.output or not args.name or not args.qualification:
        parser.error('--output, --name and --qualification required')
    prepare(args)
