import json
from pathlib import Path

import pytest

from tools import check_v2 as gate
from test_physical_gate import evidence as physical_evidence


def xml(names):
    return '<testsuite>' + ''.join(f'<testcase classname="tests.test_fixture" name="{n}"/>' for n in sorted(names)) + '</testsuite>'


def bundle(tmp_path):
    metrics, config, _, _ = physical_evidence(tmp_path)
    config['DESIGN_NAME'] = 'tt_um_mcranny_protocol_emulator_v2'
    root = tmp_path / 'bundle'
    artifacts = root / 'artifacts'
    artifacts.mkdir(parents=True)
    for name in gate.ARTIFACTS:
        (artifacts / name).write_text('fixture')
    suites = {'precheck.xml': gate.PRECHECK_SUITES, 'gatelevel.xml': gate.PIN_SUITES,
              'v2-pins.xml': gate.PIN_SUITES, 'v2-core.xml': gate.CORE_SUITES,
              'capture.xml': {'v2_capture_differential'},
              'python.xml': {f'test_{i}' for i in range(327)},
              'v1-core.xml': {'differential_directed', 'differential_random', 'differential_uart'},
              'v1-pins.xml': {'host_protocol_and_safety', 'uart_firmware_acceptance', 'host_phase_and_interrupted_uart'}}
    for name, names in suites.items():
        (artifacts / name).write_text(xml(names))
    for name in gate.ARTIFACTS:
        if name.startswith('formal-'):
            (artifacts / name).write_text('Induction step proven: SUCCESS!')
    for name, data in (('metrics.json', metrics), ('config.json', config),
                       ('mutations.json', [{'mutation': m, 'detected': True} for m in gate.MUTATIONS]),
                       ('render-manifest.json', {'source_sha256': gate.checksum(artifacts / 'layout.gds'),
                         'svg_sha256': gate.checksum(artifacts / 'layout.svg'), 'geometry_simplified': False,
                         'svg_inventory_matches_gds': True,
                         'layers_omitted': [], 'hierarchy_depth_limit': None})):
        (artifacts / name).write_text(json.dumps(data))
    (artifacts / 'python-collection.txt').write_text('\n'.join(
        f'tests/test_fixture.py::test_{i}' for i in range(327)))
    names = gate.REQUIRED_INPUTS
    inputs = {}
    for name in names:
        path = root / 'source' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(name)
        inputs[name] = gate.checksum(path)
    hashes = {p.name: gate.checksum(p) for p in artifacts.iterdir()}
    data = {'schema': 2, 'source_commit': 'a' * 40, 'inputs': inputs, 'artifacts': hashes,
            'stages': {name: {'source_commit': 'a' * 40, 'source_dirty': False, 'exit_code': 0,
                             'inputs': inputs.copy()} for name in gate.STAGES}}
    data['stages']['gatelevel']['netlist_sha256'] = hashes['netlist.v']
    data['stages']['precheck']['gds_sha256'] = hashes['layout.gds']
    (root / 'evidence.json').write_text(json.dumps(data))
    return root, data


def test_complete_v2_bundle(tmp_path):
    root, _ = bundle(tmp_path)
    assert gate.accept(root)['passing_tests'] == {'precheck': 9, 'gatelevel': len(gate.PIN_SUITES)}


@pytest.mark.parametrize('corruption', ['dirty', 'revision', 'inputs', 'netlist', 'gds', 'failed',
                                      'missing_stage', 'missing_artifact', 'changed_source',
                                      'v1_suites', 'skipped', 'render', 'proof', 'mutation',
                                      'python_missing', 'python_duplicate', 'python_uncollected'])
def test_reject_incomplete_or_mixed_evidence(tmp_path, corruption):
    root, data = bundle(tmp_path)
    stage = data['stages']['gatelevel']
    if corruption == 'dirty': stage['source_dirty'] = True
    elif corruption == 'revision': stage['source_commit'] = 'b' * 40
    elif corruption == 'inputs': stage['inputs'] = {}
    elif corruption == 'netlist': stage['netlist_sha256'] = 'b' * 64
    elif corruption == 'gds': data['stages']['precheck']['gds_sha256'] = 'b' * 64
    elif corruption == 'failed': stage['exit_code'] = 1
    elif corruption == 'missing_stage': del data['stages']['precheck']
    elif corruption == 'missing_artifact': (root / 'artifacts/layout.gds').unlink()
    elif corruption == 'changed_source': (root / 'source/src/v2_engine.v').write_text('modified')
    else:
        name, text = {
            'v1_suites': ('gatelevel.xml', xml({'host_protocol_and_safety'})),
            'skipped': ('v2-pins.xml', xml(gate.PIN_SUITES).replace('</testsuite>', '<skipped/></testsuite>')),
            'render': ('render-manifest.json', '{}'),
            'proof': ('formal-v2-integration.log', 'proof failed'),
            'mutation': ('mutations.json', '[]'),
            'python_missing': ('python.xml', xml({'test_0'})),
            'python_duplicate': ('python.xml', xml({'test_0'}).replace('</testsuite>', '<testcase classname="tests.test_fixture" name="test_0"/></testsuite>')),
            'python_uncollected': ('python-collection.txt', 'tests/test_fixture.py::different'),
        }[corruption]
        (root / 'artifacts' / name).write_text(text)
        data['artifacts'][name] = gate.checksum(root / 'artifacts' / name)
    (root / 'evidence.json').write_text(json.dumps(data))
    with pytest.raises(ValueError):
        gate.accept(root)
