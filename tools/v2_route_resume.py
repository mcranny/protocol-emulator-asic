"""Bounded incremental DRC repair from a retained ODB, using the pinned router.

This is diagnostic routing only. It cannot establish extracted timing or release
acceptance. Every batch ends with an independent whole-layout DRC check.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

try:
    from . import v2_closure as closure
    from .v2_route_monitor import drc_summary, sample
    from .v2_route_trial import verify_timing, resource_reason, stop_child
except ImportError:
    import v2_closure as closure
    from v2_route_monitor import drc_summary, sample
    from v2_route_trial import verify_timing, resource_reason, stop_child

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = "runs/route/01-openroad-detailedrouting/drt_iter54.odb"
REPORT = "runs/route/01-openroad-detailedrouting/drt-run-0/tt_um_mcranny_protocol_emulator_v2.drc-54.rpt"


def repair_script(input_odb, directory, batch):
    # INCR=3 reconstructs existing routing; DRC=0 repairs violating nets only.
    # Never use ALL=1. The pinned flow may still label DRC repair optimization
    # until it falls below its internal 100-marker threshold.
    size = (9, 11, 13, 7)[batch % 4]
    return f"""read_db {input_odb}
set_routing_layers -signal Metal2-Metal4 -clock Metal2-Metal4
detailed_route -single_step_dr -or_seed 43 -or_k 1 -verbose 1
drt::step_dr 7 0 1 1 1 100 0.95 3 false
drt::step_dr {size} 0 8 8 8 100 0.95 0 false
drt::step_dr {size} -2 8 8 8 100 0.95 0 false
drt::step_dr {size} -4 8 8 8 100 0.95 0 false
drt::step_end
write_db {directory}/drt_iter{batch}.odb
drt::check_drc -output_file {directory}/drt-run-{batch}/whole-layout.drc.rpt
puts "RESUME_BATCH_COMPLETE {batch}"
"""


def inside():
    root = Path('/closure')
    m = closure.read(root / 'manifest.json')
    for name, digest in m['prepared_sha256'].items():
        if closure.sha(root / name) != digest:
            raise ValueError(f'prepared input changed: {name}')
    source = Path('/previous') / SNAPSHOT
    if closure.sha(source) != m['snapshot_sha256']:
        raise ValueError('source snapshot changed')
    verify_timing(Path('/timing'))
    status = dict(phase='running', started_unix=time.time(), passed=False,
                  release_acceptance=False, diagnostic_only=True, stop_reason=None,
                  baseline_violations=m['baseline_violations'], batches=[])
    closure.write(root / 'route-status.json', status)
    best, unchanged = m['baseline_violations'], 0
    step = root / 'runs/repair/01-openroad-detailedrouting'
    step.mkdir(parents=True)
    try:
        for batch in range(8):
            directory = step / f'drt-run-{batch}'
            directory.mkdir()
            script = root / f'batch-{batch}.tcl'
            script.write_text(repair_script(source, step, batch))
            command = ['openroad', '-exit', '-no_splash', '-threads', '6', str(script)]
            closure.write(directory / 'command.json', command)
            with (root / 'flow.log').open('a') as log:
                child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True)
                try:
                    while child.poll() is None:
                        resource, _ = sample(root)
                        reason = resource_reason(resource, m['memory_stop_bytes'])
                        if time.time() - status['started_unix'] > 2 * 3600:
                            reason = reason or 'two-hour incremental repair limit'
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
            if child.returncode:
                status['stop_reason'] = status['stop_reason'] or f'OpenROAD exited {child.returncode}'
                break
            output = step / f'drt_iter{batch}.odb'
            report = directory / 'whole-layout.drc.rpt'
            summary = drc_summary(report.read_text())
            count = summary['count']
            status['batches'].append(dict(batch=batch, violations=count,
                snapshot=str(output.relative_to(root)), sha256=closure.sha(output),
                report_sha256=closure.sha(report)))
            closure.write(root / 'route-status.json', status)
            if count == 0:
                best = 0
                status['stop_reason'] = 'zero router DRC; extracted timing and full acceptance still required'
                break
            if count < best:
                best, unchanged = count, 0
                source = output
            else:
                unchanged += 1
            if unchanged >= 3:
                status['stop_reason'] = 'three repair batches without improvement in whole-layout DRC'
                break
        else:
            status['stop_reason'] = 'eight-batch incremental repair budget exhausted'
    except BaseException as error:
        status['stop_reason'] = f'interrupted or failed: {type(error).__name__}: {error}'
        raise
    finally:
        sample(root)
        status.update(phase='finished', finished_unix=time.time(), best_violations=best)
        closure.write(root / 'route-status.json', status)
    return 0 if status['batches'] and status['batches'][-1]['violations'] == 0 else 2


def prepare(previous, output):
    previous, output = previous.resolve(), output.resolve()
    old = closure.read(previous / 'manifest.json')
    timing = Path(old['timing_directory'])
    tm, _, _ = verify_timing(timing, ROOT)
    baseline = drc_summary((output / 'baseline.drc.rpt').read_text())
    original = drc_summary((previous / REPORT).read_text())
    if baseline != original:
        raise ValueError('independent snapshot DRC differs from the retained report')
    if closure.read(output / 'audit-inspect.json')['State']['ExitCode'] != 0:
        raise ValueError('snapshot audit did not exit successfully')
    if closure.output('docker', 'ps', '--format', '{{.Names}}').strip():
        raise ValueError('another container is running')
    name = 'v2-closure-hold005-resume'
    if name in closure.output('docker', 'ps', '-a', '--format', '{{.Names}}').splitlines():
        raise ValueError('resume container already exists')
    info = json.loads(closure.output('docker', 'info', '--format', '{{json .}}'))
    if info['NCPU'] < 6 or info['MemTotal'] < 7 * 1024**3:
        raise ValueError('insufficient resources for six-thread resume')
    for filename in ('v2_route_resume.py', 'v2_route_trial.py', 'v2_route_monitor.py',
                     'v2_closure.py', 'local_flow.py'):
        shutil.copy2(ROOT / 'tools' / filename, output / filename)
    command = ['docker', 'run', '--detach', '--name', name, '--network', 'none',
               '-v', f'{output}:/closure', '-v', f'{previous}:/previous:ro',
               '-v', f'{timing}:/timing:ro']
    for target, host in tm['mounts'].items():
        command += ['-v', f'{host}:{target}:ro']
    command += [closure.IMAGE, 'python', '/closure/v2_route_resume.py', '--inside']
    m = dict(container_name=name, diagnostic_only=True, release_acceptance=False,
             source_run=str(previous), snapshot_sha256=closure.sha(previous / SNAPSHOT),
             source_manifest_sha256=closure.sha(previous / 'manifest.json'),
             image=closure.IMAGE, threads=6, routing_search=dict(seed=43, swaps_per_net=1),
             memory_stop_bytes=info['MemTotal'] - 1024**3,
             baseline_violations=baseline['count'], command=command,
             repair_mode='incremental and DRC-only batches; named strategy remains automatic',
             prepared_sha256={p.name: closure.sha(p) for p in output.iterdir() if p.is_file()})
    closure.write(output / 'manifest.json', m)
    m['container_id'] = closure.output(*command)
    closure.write(output / 'manifest.json', m)
    with (output / 'idle-sleep-guard.log').open('w') as log:
        guard = subprocess.Popen(['caffeinate', '-i', 'docker', 'wait', name],
                                 stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    closure.write(output / 'idle-sleep-guard.json', dict(pid=guard.pid, container=name))
    print(output)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inside', action='store_true')
    parser.add_argument('--previous', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.inside:
        raise SystemExit(inside())
    if args.previous is None or args.output is None:
        parser.error('--previous and --output are required')
    prepare(args.previous, args.output)
