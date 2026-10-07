"""Retain a minimal diode ECO and gate its continuation through layout signoff.

Only the measured redundant antenna cell is removed. Full-layout antenna/DRC,
new extraction and all-corner electrical checks must pass before signoff begins.
This checkpoint continuation remains development evidence until clean-revision
functional, gate-level, precheck and artifact provenance are assembled.
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
    from .v2_antenna_repair import relocate, repair_config
    from .v2_route_trial import extracted_issues, resource_reason, stop_child
    from .v2_route_monitor import sample
    from .validate_local import source_state
except ImportError:
    import v2_closure as closure
    from v2_antenna_repair import relocate, repair_config
    from v2_route_trial import extracted_issues, resource_reason, stop_child
    from v2_route_monitor import sample
    from validate_local import source_state

ROOT = Path(__file__).resolve().parents[1]
TOP = 'tt_um_mcranny_protocol_emulator_v2'


def baseline_issues(metrics):
    expected = [f'design__max_fanout_violation__count__corner:{c} = 1' for c in closure.CORNERS]
    issues = extracted_issues(metrics)
    if sorted(issues) != sorted(expected):
        raise ValueError(f'expected only one fanout violation per corner: {issues}')


def eco_script():
    return '''source $::env(SCRIPTS_DIR)/openroad/common/io.tcl
read_current_odb
set net [$::block findNet net3847]
set inst [$::block findInst ANTENNA_123]
if {$net == "NULL" || $inst == "NULL"} {error "ECO target missing"}
if {[[$inst getMaster] getName] ne "sg13cmos5l_antennanp"} {error "ECO target is not a diode"}
if {[[$inst findITerm A] getNet] != $net} {error "ECO diode net mismatch"}
set loads 0
foreach pin [$net getITerms] {
    if {[$pin getIoType] == "INPUT" && [$pin getSigType] == "SIGNAL"} {incr loads}
}
if {$loads != 9} {error "ECO expected nine input loads, found $loads"}
puts "ECO_REMOVE ANTENNA_123 net3847 loads_before=$loads"
odb::dbInst_destroy $inst
set remaining [check_antennas -report_file $::env(STEP_DIR)/eco-antennas.rpt]
if {$remaining != 0} {error "ECO causes antenna violations: $remaining"}
set report $::env(STEP_DIR)/$::env(DESIGN_NAME).drc
drt::check_drc -output_file $report
if {![file exists $report]} {error "ECO whole-layout DRC report missing"}
if {[file size $report] != 0} {
    # Removing a diode can expose a minimum-area pin-access remnant. Repair
    # existing wires with bounded incremental/DRC passes; never full rip-up.
    set_routing_layers -signal Metal2-Metal4 -clock Metal2-Metal4
    detailed_route -single_step_dr -or_seed 43 -or_k 1 -verbose 1
    drt::step_dr 7 0 1 1 1 100 0.95 3 false
    drt::step_dr 9 0 8 8 8 100 0.95 0 false
    drt::step_dr 9 -2 8 8 8 100 0.95 0 false
    drt::step_end
    drt::check_drc -output_file $report
}
if {[file size $report] != 0} {error "ECO whole-layout DRC is not empty"}
if {[check_antennas -report_file $::env(STEP_DIR)/eco-final-antennas.rpt] != 0} {
    error "ECO routing repair violates antenna limits"
}
utl::metric_integer route__drc_errors 0
puts "ECO_AUDIT_PASS antenna=0 whole_layout_drc=0"
write_views
'''


def flow():
    import runpy
    from librelane.steps.openroad import DetailedRouting
    script = Path('/closure/eco.tcl')
    script.write_text(eco_script())
    DetailedRouting.get_script_path = lambda self: str(script)
    runpy.run_module('librelane', run_name='__main__')


def inside():
    root = Path('/closure')
    m = closure.read(root / 'manifest.json')
    for name, digest in m['prepared_sha256'].items():
        if closure.sha(root / name) != digest:
            raise ValueError(f'prepared input changed: {name}')
    for name, digest in m['resume_artifact_sha256'].items():
        if closure.sha(name) != digest:
            raise ValueError(f'checkpoint changed: {name}')
    status = dict(phase='running', stage='diode ECO and independent whole-layout audit',
                  started_unix=time.time(), passed=False, release_acceptance=False, stop_reason=None)
    def run(command, stage):
        status['stage'] = stage
        closure.write(root / 'route-status.json', status)
        with (root / 'flow.log').open('a') as log:
            log.write('\n=== ' + stage + ' ===\n'); log.flush()
            child = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            try:
                while child.poll() is None:
                    resource, _ = sample(root)
                    reason = resource_reason(resource, m['memory_stop_bytes'])
                    if time.time() - status['started_unix'] > 12 * 3600:
                        reason = reason or '12-hour continuation limit'
                    if reason:
                        status['stop_reason'] = reason
                        stop_child(child)
                        raise RuntimeError(reason)
                    try:
                        child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                if child.poll() is None:
                    stop_child(child)
        if child.returncode:
            raise RuntimeError(f'{stage} exited {child.returncode}')
    common = ['--manual-pdk', '--pdk-root', '/pdk', '--pdk', 'ihp-sg13cmos5l', '-j', '6']
    try:
        run([sys.executable, '/closure/v2_fanout_repair.py', '--flow', *common,
             '--run-tag', 'route', '--from', 'OpenROAD.DetailedRouting', '--to', 'OpenROAD.STAPostPNR',
             '--with-initial-state', '/closure/state.json', '/closure/resolved.json'],
            'diode ECO, whole-layout audit and fresh extracted timing')
        metrics = closure.read(root / 'runs/route/final/metrics.json')
        issues = extracted_issues(metrics)
        closure.write(root / 'electrical-gate.json', dict(passed=not issues, issues=issues))
        if issues:
            raise ValueError(f'fresh electrical gate failed: {issues}')
        run(['openroad', '-exit', '-no_splash', '-python', '/closure/v2_antenna_connectivity.py',
             f'/closure/runs/route/final/odb/{TOP}.odb', '/closure/diode-connectivity.json'],
            'checking remaining diode connectivity')
        checkpoints = list((root / 'runs/route').glob('*-openroad-stapostpnr/state_out.json'))
        if len(checkpoints) != 1:
            raise ValueError('expected unique extracted timing checkpoint')
        run([sys.executable, '-m', 'librelane', *common, '--run-tag', 'signoff',
             '--from', 'OpenROAD.IRDropReport', '--with-initial-state', str(checkpoints[0]),
             '/closure/resolved.json'], 'final layout, power, DRC and LVS signoff')
        metrics = closure.read(root / 'runs/signoff/final/metrics.json')
        issues = extracted_issues(metrics)
        for key in ('magic__drc_error__count', 'design__lvs_error__count'):
            if metrics.get(key) != 0:
                issues.append(f'missing/nonzero signoff metric {key}: {metrics.get(key)}')
        status.update(passed=not issues, issues=issues)
        if issues:
            status['stop_reason'] = 'signoff gates failed'
    except BaseException as error:
        status['stop_reason'] = status['stop_reason'] or f'{type(error).__name__}: {error}'
        raise
    finally:
        status.update(phase='finished', finished_unix=time.time())
        closure.write(root / 'route-status.json', status)
        sample(root)
    return 0 if status['passed'] else 2


def prepare(args):
    previous, dest = args.previous.resolve(), args.output.resolve()
    old = closure.read(previous / 'manifest.json')
    # Preserve the predecessor's earlier mounts, without writable access.
    mounts = {}
    for i, value in enumerate(old['command']):
        if value == '-v':
            host, target, *_ = old['command'][i+1].split(':')
            if target not in ('/closure', '/closure/tt'):
                mounts[target] = Path(host)
    mounts['/closure'] = previous
    pdk, tt = ROOT / 'build/deps/v2-pdk', ROOT / 'build/deps/v2-tt'
    mounts['/closure/tt'] = tt
    for path, revision in ((pdk, closure.PDK_REV), (tt, closure.TT_REV)):
        closure.verify_checkout(path, revision)
    for key, expected in (('image', closure.IMAGE), ('pdk_commit', closure.PDK_REV), ('support_tools_commit', closure.TT_REV)):
        if old.get(key) != expected:
            raise ValueError(f'dependency mismatch: {key}')
    for name, digest in old['prepared_sha256'].items():
        if closure.sha(previous / name) != digest:
            raise ValueError(f'predecessor input changed: {name}')
    for name in closure.RTL:
        if closure.sha(ROOT / 'src' / name) != closure.sha(previous / 'src' / name):
            raise ValueError(f'RTL changed: {name}')
    metrics = closure.read(previous / 'runs/route/final/metrics.json')
    baseline_issues(metrics)
    cfg = repair_config(closure.read(previous / 'runs/route/resolved.json'))
    checkpoint = previous / 'runs/route/03-openroad-checkantennas-1/state_out.json'
    state = closure.read(checkpoint)
    # Keep verified upstream synthesis metrics; all route/electrical checks are freshly generated.
    artifacts = {}
    def map_paths(v):
        if isinstance(v, str) and v.startswith('/'):
            host = closure.mapped_path(v, mounts)
            relative = 'inputs/' + closure.sha(host)[:16] + '-' + host.name
            artifacts[relative] = host
            return '/closure/' + relative
        if isinstance(v, dict):return {k:map_paths(x) for k,x in v.items()}
        if isinstance(v, list):return [map_paths(x) for x in v]
        return v
    state = map_paths(state)
    if closure.output('docker', 'ps', '--format', '{{.Names}}'):
        raise ValueError('another container is running')
    info = json.loads(closure.output('docker', 'info', '--format', '{{json .}}'))
    if info['NCPU'] < 6 or info['MemTotal'] < 7 * 1024**3 or shutil.disk_usage(ROOT).free < 20 * 1024**3:
        raise ValueError('insufficient resources')
    dest.mkdir(parents=True, exist_ok=False)
    (dest / 'inputs').mkdir()
    for relative, host in artifacts.items():shutil.copy2(host, dest / relative)
    shutil.copytree(previous / 'src', dest / 'src')
    names = ('v2_fanout_repair.py', 'v2_antenna_repair.py', 'v2_antenna_connectivity.py',
             'v2_closure.py', 'v2_route_trial.py', 'v2_route_monitor.py', 'local_flow.py',
             'validate_local.py', 'check_v2.py', 'check_physical.py')
    for name in names:shutil.copy2(ROOT / 'tools' / name, dest / name)
    closure.write(dest / 'resolved.json', cfg)
    closure.write(dest / 'state.json', state)
    closure.write(dest / 'baseline-metrics.json', metrics)
    closure.write(dest / 'predecessor.json', dict(directory=str(previous), state_sha256=closure.sha(checkpoint),
        metrics_sha256=closure.sha(previous / 'runs/route/final/metrics.json'), inputs={k:closure.sha(v) for k,v in artifacts.items()}))
    command = ['docker', 'run', '--detach', '--name', args.name, '--network', 'none',
        '-v', f'{dest}:/closure', '-v', f'{pdk}:/pdk:ro', '-v', f'{tt}:/closure/tt:ro',
        '-w', '/closure', closure.IMAGE, 'python', '/closure/v2_fanout_repair.py', '--inside']
    manifest = dict(schema=1, release_acceptance=False, **source_state(ROOT), container_name=args.name,
        image=closure.IMAGE, pdk_commit=closure.PDK_REV, support_tools_commit=closure.TT_REV,
        threads=6, repair_mode='remove one qualified redundant diode; preserve routing',
        baseline_antenna_nets=0, command=command, resume_artifact_sha256={},
        memory_stop_bytes=info['MemTotal']-1024**3,
        prepared_sha256={str(p.relative_to(dest)):closure.sha(p) for p in dest.rglob('*') if p.is_file()})
    closure.write(dest / 'manifest.json', manifest)
    container = closure.output(*command)
    closure.write(dest / 'launch.json', dict(container_id=container))
    with (dest / 'sleep-guard.log').open('w') as log:
        guard = subprocess.Popen(['caffeinate', '-i', 'docker', 'wait', args.name],
            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    closure.write(dest / 'sleep-guard.json', dict(pid=guard.pid, container=args.name))
    print(container)


if __name__ == '__main__':
    if '--flow' in sys.argv:
        sys.argv.remove('--flow'); flow()
    elif '--inside' in sys.argv:
        raise SystemExit(inside())
    else:
        p = argparse.ArgumentParser(description=__doc__)
        p.add_argument('--previous', type=Path, required=True)
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--name', required=True)
        prepare(p.parse_args())
