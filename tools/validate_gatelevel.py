"""Run every V2 pin suite locally against one compiled Icarus netlist.

Each suite runs in an independent simulator process with its own log and XML.
The merged acceptance result is written only after every exact suite passes.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

try:
    from .check_v2 import PIN_SUITES, checksum, passing_xml
except ImportError:
    from check_v2 import PIN_SUITES, checksum, passing_xml

ROOT = Path(__file__).resolve().parents[1]


def selected_case(path, name):
    """Validate a filtered cocotb report, retaining its one executed test.

    Cocotb also reports the other collected tests as skipped. Those entries
    must be exactly the other known suites; each is required to execute in its
    own process before the final result can be written.
    """
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    names = [case.get('name') for case in cases]
    if set(names) != PIN_SUITES or len(names) != len(PIN_SUITES):
        raise ValueError(f'missing, duplicate or unexpected collected suites: {path}')
    if any(list(root.iter(tag)) for tag in ('failure', 'error')):
        raise ValueError(f'failed or errored shard: {path}')
    if any(int(suite.get(key, '0')) for suite in root.iter('testsuite')
           for key in ('failures', 'errors')):
        raise ValueError(f'nonpassing shard summary: {path}')
    selected = None
    for case in cases:
        simulation_time = float(case.get('sim_time_ns', '0'))
        if not math.isfinite(simulation_time) or simulation_time < 0:
            raise ValueError(f'invalid simulation time: {path}')
        if case.get('name') == name:
            if case.find('skipped') is not None or simulation_time == 0:
                raise ValueError(f'assigned suite did not execute: {name}')
            selected = case
        elif (len(case.findall('skipped')) != 1
              or simulation_time != 0):
            raise ValueError(f'unassigned suite executed: {case.get("name")}')
    if selected is None:
        raise ValueError(f'missing assigned suite: {name}')
    return selected


def validate(netlist, pdk_root, destination, jobs=3):
    if not 1 <= jobs <= 4:
        raise ValueError('jobs must be 1..4')
    netlist, pdk_root, destination = (
        Path(path).resolve() for path in (netlist, pdk_root, destination))
    netlist_hash = checksum(netlist)
    destination.mkdir(parents=True, exist_ok=False)
    sim = destination / 'sim'
    environment = os.environ.copy()
    environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment['PATH']
    environment['COCOTB_RANDOM_SEED'] = '20260926'
    for key in ('COCOTB_TESTCASE', 'TESTCASE', 'COCOTB_TEST_FILTER', 'COMPILE_ARGS', 'EXTRA_ARGS'):
        environment.pop(key, None)
    base = ['make', '-C', str(ROOT / 'test'), '-f', 'Makefile.v2pins',
            'GATES=yes', 'SIM=icarus', f'PDK_ROOT={pdk_root}',
            f'GATE_LEVEL_NETLIST={netlist}', f'SIM_BUILD={sim}']
    compile_command = [*base, str(sim / 'sim.vvp')]
    with (destination / 'compile.log').open('w') as log:
        subprocess.run(compile_command, env=environment, stdout=log,
                       stderr=subprocess.STDOUT, check=True)

    def run(name):
        result = destination / (name + '.xml')
        command = [*base, f'COCOTB_RESULTS_FILE={result}', f'COCOTB_TEST_FILTER={name}']
        with (destination / (name + '.log')).open('w') as log:
            completed = subprocess.run(command, env=environment, stdout=log,
                                       stderr=subprocess.STDOUT)
        print(f'{name}: {completed.returncode}', flush=True)
        if completed.returncode:
            raise ValueError(f'{name} failed; see its log')
        case = selected_case(result, name)
        return name, case, command

    # Start the longer suites first, but execute every suite exactly once.
    names = sorted(PIN_SUITES, key=lambda name: (
        not ('spi_target' in name or 'host_budget' in name), name))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(run, names))
    if checksum(netlist) != netlist_hash:
        raise ValueError('netlist changed during simulation')
    merged = ET.Element('testsuites')
    for name, case, _ in sorted(results):
        suite = ET.SubElement(merged, 'testsuite', name=name)
        suite.append(case)
    temporary = destination / 'gatelevel.pending.xml'
    ET.ElementTree(merged).write(temporary, encoding='utf-8', xml_declaration=True)
    passing_xml(temporary, required_names=PIN_SUITES)
    temporary.rename(destination / 'gatelevel.xml')
    manifest = {'netlist_sha256': netlist_hash, 'compile_command': compile_command,
                'commands': [item[2] for item in results]}
    (destination / 'commands.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'All {len(results)} gate-level pin suites passed.', flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--netlist', type=Path, required=True)
    parser.add_argument('--pdk-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    try:
        validate(args.netlist, args.pdk_root, args.output, args.jobs)
    except (ValueError, OSError, ET.ParseError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Gate-level validation failed: {error}\n')
