"""Measure unmapped cells in the final netlist using the pinned Liberty inventory."""
import json
from pathlib import Path
import re
import subprocess
import sys


def audit_statistics(stats, liberty, top):
    modules = stats.get('modules', {})
    module = modules.get('\\' + top, modules.get(top))
    if not isinstance(module, dict) or module.get('num_processes') != 0:
        raise ValueError('missing or unelaborated top module')
    allowed = set(re.findall(r'\bcell\s*\(\s*"?([A-Za-z0-9_]+)"?\s*\)', liberty))
    cells = module.get('num_cells_by_type')
    if not allowed or not isinstance(cells, dict) or not cells:
        raise ValueError('missing cell inventory')
    unknown = {name: count for name, count in cells.items() if name.lstrip('\\') not in allowed}
    return dict(passed=not unknown, unmapped_cells=unknown,
                unmapped_count=sum(unknown.values()), cell_count=sum(cells.values()))


def main(netlist, output):
    import hashlib
    output = Path(output)
    library = Path('/pdk/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib')
    script = output.with_suffix('.ys')
    statistics = output.with_suffix('.stat.json')
    # Inputs are prepared absolute paths without Tcl/Yosys metacharacters.
    for path in (Path(netlist), output, library):
        if re.search(r'[^a-zA-Z0-9_./-]', str(path)):
            raise ValueError('unsupported audit path')
    script.write_text(f'read_liberty -lib -ignore_miss_func -ignore_miss_dir {library}\n'
        f'read_verilog {netlist}\nhierarchy -check -top tt_um_mcranny_protocol_emulator_v2\n'
        f'check -assert\ntee -o {statistics} stat -json\n')
    subprocess.run(['yosys', '-Q', '-T', '-s', str(script)], check=True)
    result = audit_statistics(json.loads(statistics.read_text()), library.read_text(),
                              'tt_um_mcranny_protocol_emulator_v2')
    result['netlist_sha256'] = hashlib.sha256(Path(netlist).read_bytes()).hexdigest()
    result['liberty_sha256'] = hashlib.sha256(library.read_bytes()).hexdigest()
    output.write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['passed'] else 2


if __name__ == '__main__':
    raise SystemExit(main(*sys.argv[1:]))
