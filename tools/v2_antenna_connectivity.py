"""Check every antenna diode's signal and supply connections in the routed ODB."""
import json
from pathlib import Path
import sys


def audit(block):
    issues, diodes = [], []
    for inst in block.getInsts():
        if inst.getMaster().getName() != 'sg13cmos5l_antennanp':
            continue
        nets = {pin.getMTerm().getName(): pin.getNet().getName() if pin.getNet() else None
                for pin in inst.getITerms()}
        name = inst.getName()
        diodes.append(dict(instance=name, nets=nets))
        for pin, net in (('VDD', 'VPWR'), ('VSS', 'VGND')):
            if nets.get(pin) != net:
                issues.append(f'{name}/{pin} is not connected to {net}')
        if not nets.get('A') or nets['A'] in ('VPWR', 'VGND'):
            issues.append(f'{name}/A lacks a signal connection')
    if not diodes:
        issues.append('no antenna diodes found')
    return dict(passed=not issues, issues=issues, count=len(diodes), diodes=diodes,
                scope='ODB net connectivity; physical rail contact still requires signoff')


if __name__ == '__main__':
    import odb
    db = odb.dbDatabase.create()
    odb.read_db(db, sys.argv[1])
    result = audit(db.getChip().getBlock())
    Path(sys.argv[2]).write_text(json.dumps(result, indent=2) + '\n')
    raise SystemExit(0 if result['passed'] else 2)
