import ast
import copy
import hashlib
import json
from pathlib import Path
import sys

import pytest

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
OUT = ROOT / 'build/release-preparation'
record = json.loads((Path(__file__).parent / 'frozen.json').read_text())
baseline = json.loads((OUT / 'baseline/stage.json').read_text())
suite_names = {row['new'].split('::')[1]: row['old'].split('::')[1] for row in record['cocotb']}
path_names = {row['new'].split('::')[0]: row['old'].split('::')[0] for row in record['pytest'] + record['cocotb']}
factories = {
    'make_engine': ('engine', ()), 'make_model': ('loaded', ()),
    'make_fault_engine': ('loaded', ()), 'make_i2c_engine': ('loaded', ()),
    'make_project': ('project', ()), 'make_routed_metrics': ('routed_metrics', ('tmp_path',)),
    'make_repository': ('repository', ('tmp_path',)),
    'make_gatelevel_run': ('setup_run', ('tmp_path', 'monkeypatch')),
    'make_stage': ('stage', ('tmp_path', 'monkeypatch')),
    'make_bundle': ('bundle', ('tmp_path',)),
    'make_physical_evidence': ('evidence', ('tmp_path',)),
}


class Normalize(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, str):
            value = node.value
            for new, old in sorted({**suite_names, **path_names}.items(), key=lambda p: len(p[0]), reverse=True):
                value = value.replace(new, old)
            node.value = value
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id in factories:
            old, injected = factories[node.func.id]
            node.func.id = old
            node.args = [ast.Name(id=name, ctx=ast.Load()) for name in injected] + node.args
        return node

    def visit_Import(self, node):
        return None

    def visit_ImportFrom(self, node):
        return None


def body(node, normalize=False):
    value = ast.Module(body=copy.deepcopy(node.body), type_ignores=[])
    if normalize:
        value = Normalize().visit(value)
    else:
        # Import sorting/removal is not a behavior change.
        class Imports(ast.NodeTransformer):
            def visit_Import(self, node): return None
            def visit_ImportFrom(self, node): return None
        value = Imports().visit(value)
    return ast.dump(value, include_attributes=False)


class Verify:
    def pytest_collection_finish(self, session):
        expected = [row['new'].replace('test_release_requirements_is_not_weakened', 'test_release_requirements_are_not_weakened') for row in record['pytest']]
        actual = [item.nodeid for item in session.items]
        assert len(set(expected)) == len(expected) == len(actual) == 583
        assert set(actual) == set(expected), {'missing': sorted(set(expected) - set(actual)), 'extra': sorted(set(actual) - set(expected))}
        mismatches, assertions = [], 0
        for key, definition in record['definitions'].items():
            old_path, old_name = key.split('::')
            new_name = old_name.replace('contract_is', 'requirements_are')
            old_tree = ast.parse((OUT / 'baseline/source' / old_path).read_text())
            new_tree = ast.parse((ROOT / definition['destination']).read_text())
            old = next(n for n in old_tree.body if isinstance(n, ast.FunctionDef) and n.name == old_name)
            new = next(n for n in new_tree.body if isinstance(n, ast.FunctionDef) and n.name == new_name)
            if key == 'tests/test_physical_config.py::test_pinned_nominal_rc_values_populate_routed_resizer_tables':
                # Exactly one repository-root lookup moves with this module.
                old = ast.parse(ast.unparse(old).replace('Path(__file__).parents[1]', 'Path(__file__).parents[2]')).body[0]
            if body(old) != body(new, True):
                mismatches.append(key)
            # Parameter values themselves must remain byte-for-byte equivalent ASTs.
            old_marks = [[ast.dump(a) for a in d.args] for d in old.decorator_list]
            new_marks = [[ast.dump(a) for a in d.args] for d in new.decorator_list]
            assert old_marks == new_marks, key
            assertions += len(definition['assertions'])
        for row in record['cocotb']:
            old_path, old_name = row['old'].split('::')
            new_path, new_name = row['new'].split('::')
            old = next(n for n in ast.parse((OUT / 'baseline/source' / old_path).read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == old_name)
            new = next(n for n in ast.parse((ROOT / new_path).read_text()).body if isinstance(n, ast.AsyncFunctionDef) and n.name == new_name)
            if old_path == 'test/test_v2_pins.py' and old_name in ('v2_uart_fault_capture_and_scenario', 'v2_spi_i2c_fault_scenarios'):
                # Preserve the existing test/output artifact directory after relocation.
                old = ast.parse(ast.unparse(old).replace('Path(__file__).parent', 'Path(__file__).parents[1]')).body[0]
            if body(old) != body(new, True): mismatches.append(row['old'])
            assertions += len(row['assertions'])
        helper_count = 0
        helper_moves = [
            ('tests/test_v2_contract.py', 'engine', 'tests/v2/conftest.py', 'make_engine'),
            ('tests/test_model.py', 'loaded', 'tests/v1/test_model.py', 'make_model'),
            ('tests/test_v2_faults.py', 'loaded', 'tests/v2/test_faults.py', 'make_fault_engine'),
            ('tests/test_v2_i2c.py', 'loaded', 'tests/v2/test_i2c.py', 'make_i2c_engine'),
            ('tests/test_local_flow.py', 'project', 'tests/tooling/test_local_flow.py', 'make_project'),
            ('tests/test_local_flow.py', 'routed_metrics', 'tests/tooling/test_local_flow.py', 'make_routed_metrics'),
            ('tests/test_local_validation.py', 'repository', 'tests/tooling/test_local_validation.py', 'make_repository'),
            ('tests/test_gatelevel_runner.py', 'setup_run', 'tests/tooling/test_gatelevel_runner.py', 'make_gatelevel_run'),
            ('tests/test_v2_evidence.py', 'stage', 'tests/tooling/test_v2_evidence.py', 'make_stage'),
            ('tests/test_v2_acceptance.py', 'bundle', 'tests/tooling/test_v2_acceptance.py', 'make_bundle'),
            ('tests/test_physical_gate.py', 'evidence', 'tests/support/evidence.py', 'build_physical_evidence'),
        ]
        for old_path, new_path in {
            'test/test.py': 'test/support/pins.py',
            'test/test_engine.py': 'test/support/v1_engine.py',
            'test/test_v2_engine.py': 'test/support/v2_engine.py',
            'test/test_v2_pins.py': 'test/support/v2_pins.py',
        }.items():
            for node in ast.parse((OUT / 'baseline/source' / old_path).read_text()).body:
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not node.decorator_list:
                    helper_moves.append((old_path, node.name, new_path, node.name))
        for old_path, old_name, new_path, new_name in helper_moves:
            old = next(n for n in ast.parse((OUT / 'baseline/source' / old_path).read_text()).body if getattr(n, 'name', '') == old_name)
            new = next(n for n in ast.parse((ROOT / new_path).read_text()).body if getattr(n, 'name', '') == new_name)
            if new_name.startswith('make_'):
                new = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == 'create')
            old_body = body(old)
            if old_name == 'bundle':
                old_body = old_body.replace("id='physical_evidence'", "id='build_physical_evidence'")
            if old_body != body(new, True):
                mismatches.append(old_path + '::' + old_name)
            helper_count += 1
        protected = [name for name in baseline['inputs'] if name.startswith(('src/', 'formal/', 'physical/', 'host/', 'evidence/')) or name == 'test/tb.v']
        assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == baseline['inputs'][name] for name in protected)
        result = {'pytest_cases': len(actual), 'cocotb_scenarios': len(record['cocotb']), 'assertions': assertions,
                  'moved_helpers': helper_count, 'protected_files_unchanged': len(protected), 'body_mismatches': mismatches}
        print(json.dumps(result, indent=2))
        (OUT / 'migration-check.json').write_text(json.dumps(result, indent=2) + '\n')
        assert not mismatches, mismatches


sys.exit(pytest.main(['--collect-only', '-q'], plugins=[Verify()]))
