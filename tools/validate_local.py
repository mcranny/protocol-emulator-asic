"""Run the complete local functional gate into a new, immutable evidence directory.

Dirty runs are useful for development but cannot establish final acceptance.
Use --require-clean for release evidence. No command in this runner pushes code.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from .check_v2 import checksum, python_suites, passing_xml, PIN_SUITES, CORE_SUITES
except ImportError:
    from check_v2 import checksum, python_suites, passing_xml, PIN_SUITES, CORE_SUITES

ROOT = Path(__file__).resolve().parents[1]


def source_state(root=None):
    root = ROOT if root is None else root
    def git(*args):
        return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
    names = git('ls-files', '--cached', '--others', '--exclude-standard', '-z').split('\0')
    inputs = {name: checksum(root / name) for name in sorted(set(names))
              if name and (root / name).is_file()}
    return {'source_commit': git('rev-parse', 'HEAD'),
            'source_dirty': bool(git('status', '--porcelain')), 'inputs': inputs}


def validate(destination, require_clean=False):
    before = source_state()
    if require_clean and before['source_dirty']:
        raise ValueError('release validation requires a clean source checkout')
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    artifacts = destination / 'artifacts'
    artifacts.mkdir()
    for name in before['inputs']:
        target = destination / 'source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    manifest = dict(before, schema=1, stage='functional', exit_code=None, commands=[])
    manifest_path = destination / 'stage.json'

    def save():
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + '\n')

    def run(name, command):
        print(f'Running {name}', flush=True)
        with (artifacts / name).open('w') as log:
            result = subprocess.run(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT,
                                    env=environment)
        manifest['commands'].append({'log': name, 'argv': command, 'exit_code': result.returncode})
        save()
        if result.returncode:
            raise ValueError(f'{name} failed with exit code {result.returncode}')

    environment = os.environ.copy()
    environment['PATH'] = str(Path(sys.executable).parent) + os.pathsep + environment['PATH']
    for name in ('COCOTB_TEST_FILTER', 'COCOTB_TESTCASE', 'TESTCASE', 'GATES'):
        environment.pop(name, None)
    environment['COCOTB_RANDOM_SEED'] = '20260926'
    save()
    try:
        for executable, args in (('python', [sys.executable, '--version']),
                                 ('iverilog', ['iverilog', '-V']),
                                 ('verilator', ['verilator', '--version']),
                                 ('yosys', ['yosys', '-V'])):
            run(f'version-{executable}.log', args)
        run('python-collection.txt', [sys.executable, '-m', 'pytest', '--collect-only', '-q'])
        run('python.log', [sys.executable, '-m', 'pytest', '-q',
                           f'--junitxml={artifacts / "python.xml"}'])
        python_suites(artifacts / 'python.xml', artifacts / 'python-collection.txt')
        for top in ('tt_um_mcranny_protocol_emulator', 'tt_um_mcranny_protocol_emulator_v2'):
            sources = sorted(str(path.relative_to(ROOT)) for path in (ROOT / 'src').glob('*.v'))
            run(f'lint-{top}.log', ['verilator', '--lint-only', '--Wno-DECLFILENAME',
                                     '--top-module', top, *sources])
            run(f'synth-{top}.log', ['yosys', '-Q', '-T', '-p',
                                     f'read_verilog src/*.v; synth -top {top}; stat'])
        for name, makefile, suites in (
            ('v1-core', 'Makefile.core', {'differential_directed', 'differential_random', 'differential_uart'}),
            ('v1-pins', 'Makefile', {'host_protocol_and_safety', 'uart_firmware_acceptance', 'host_phase_and_interrupted_uart'}),
            ('v2-core', 'Makefile.v2', CORE_SUITES),
            ('v2-pins', 'Makefile.v2pins', PIN_SUITES),
            ('capture', 'Makefile.v2capture', {'v2_capture_differential'})):
            run(f'{name}.log', ['make', '-C', 'test', '-f', makefile,
                               f'SIM_BUILD={destination / "sim" / name}',
                               f'COCOTB_RESULTS_FILE={artifacts / (name + ".xml")}'])
            passing_xml(artifacts / (name + '.xml'), required_names=suites)
        for name in ('engine', 'v2-engine', 'v2-capture', 'v2-integration', 'v2-input-filter'):
            run(f'formal-{name}.log', ['yosys', '-Q', '-T', '-s',
                                      'formal/' + name.replace('-', '_') + '.ys'])
            if 'Induction step proven: SUCCESS!' not in (artifacts / f'formal-{name}.log').read_text():
                raise ValueError(f'{name} induction not proven')
        run('mutation-run.log', [sys.executable, 'tools/v2_mutations.py',
                                 '--output', str(destination / 'mutations')])
        shutil.copy2(destination / 'mutations/results.json', artifacts / 'mutations.json')
        if source_state() != before:
            raise ValueError('source changed during validation; rerun from a stable checkout')
        manifest['exit_code'] = 0
    except (ValueError, OSError) as error:
        manifest['exit_code'] = 1
        manifest['error'] = str(error)
        raise
    finally:
        manifest['artifacts'] = {path.name: checksum(path) for path in artifacts.iterdir() if path.is_file()}
        save()
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--require-clean', action='store_true')
    args = parser.parse_args()
    try:
        result = validate(args.output, args.require_clean)
        print(json.dumps({key: result[key] for key in ('source_commit', 'source_dirty', 'exit_code')}, indent=2))
    except (ValueError, OSError) as error:
        parser.exit(1, f'Local validation failed: {error}\n')
