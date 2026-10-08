import subprocess
import xml.etree.ElementTree as ET

import pytest

from tools import validate_gatelevel as runner


def setup_run(tmp_path, monkeypatch, fault=None):
    netlist = tmp_path / 'design.v'
    netlist.write_text('module design; endmodule\n')
    commands = []

    def execute(command, **kwargs):
        commands.append(command)
        args = dict(part.split('=', 1) for part in command if '=' in part)
        if 'COCOTB_RESULTS_FILE' not in args:
            if fault == 'compile':
                raise subprocess.CalledProcessError(1, command)
        else:
            name = args['COCOTB_TEST_FILTER']
            root = ET.Element('testsuites')
            suite = ET.SubElement(root, 'testsuite')
            case = None
            for collected in sorted(runner.PIN_SUITES):
                entry = ET.SubElement(suite, 'testcase', name=collected,
                                      classname='test_v2_pins',
                                      sim_time_ns='10' if collected == name else '0')
                if collected == name:
                    case = entry
                else:
                    ET.SubElement(entry, 'skipped')
            if fault == 'skip':
                ET.SubElement(case, 'skipped')
            if fault == 'duplicate':
                ET.SubElement(suite, 'testcase', name=name)
            if fault == 'summary_failure':
                suite.set('failures', '1')
            if fault == 'invalid_time':
                case.set('sim_time_ns', 'NaN')
            if fault == 'zero_time':
                case.set('sim_time_ns', '0')
            if fault == 'unassigned_executed':
                other = next(entry for entry in suite if entry.get('name') != name)
                other.remove(other.find('skipped'))
                other.set('sim_time_ns', '10')
            if fault != 'missing':
                ET.ElementTree(root).write(args['COCOTB_RESULTS_FILE'])
            if fault == 'changed_netlist':
                netlist.write_text('changed netlist\n')
            if fault == 'simulation':
                return subprocess.CompletedProcess(command, 2)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, 'run', execute)
    return netlist, commands


def test_every_suite_uses_one_compiled_netlist_and_an_isolated_result(tmp_path, monkeypatch):
    netlist, commands = setup_run(tmp_path, monkeypatch)
    output = tmp_path / 'results'
    runner.validate(netlist, tmp_path / 'pdk', output, jobs=3)
    assert len(commands) == 1 + len(runner.PIN_SUITES)
    assert all(f'GATE_LEVEL_NETLIST={netlist}' in command for command in commands)
    assert len({part for command in commands for part in command
                if part.startswith('SIM_BUILD=')}) == 1
    assert len({part for command in commands for part in command
                if part.startswith('COCOTB_RESULTS_FILE=')}) == len(runner.PIN_SUITES)
    assert runner.passing_xml(output / 'gatelevel.xml', required_names=runner.PIN_SUITES) == 14


@pytest.mark.parametrize('fault', ['compile', 'simulation', 'skip', 'duplicate', 'missing',
                                  'unassigned_executed', 'changed_netlist',
                                  'summary_failure', 'invalid_time', 'zero_time'])
def test_failed_or_invalid_runs_cannot_publish_merged_acceptance(tmp_path, monkeypatch, fault):
    netlist, _ = setup_run(tmp_path, monkeypatch, fault)
    output = tmp_path / 'results'
    with pytest.raises((ValueError, OSError, subprocess.CalledProcessError)):
        runner.validate(netlist, tmp_path / 'pdk', output, jobs=1)
    assert not (output / 'gatelevel.xml').exists()
    assert (output / 'compile.log').exists()


def test_existing_results_are_never_reused(tmp_path, monkeypatch):
    netlist, commands = setup_run(tmp_path, monkeypatch)
    output = tmp_path / 'results'
    output.mkdir()
    with pytest.raises(FileExistsError):
        runner.validate(netlist, tmp_path / 'pdk', output)
    assert commands == []


def test_selected_only_cocotb_report_preserves_exact_identity(tmp_path):
    from tools.validate_gatelevel import selected_case
    from tools.check_v2 import PIN_SUITES
    name = sorted(PIN_SUITES)[0]
    path = tmp_path / 'filtered.xml'
    path.write_text(f'<testsuites><testsuite><testcase name="{name}" sim_time_ns="10"/></testsuite></testsuites>')
    assert selected_case(path, name).get('name') == name
    with pytest.raises(ValueError, match='collected suites'):
        selected_case(path, sorted(PIN_SUITES)[1])
