"""Prepare an isolated one-macro CMOS5L qualification run. Never edits V2 inputs."""
import argparse, hashlib, json, shutil, sys
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'tools'))
from local_flow import IMAGE, PDK_REV, TT_REV, verify_checkout
NAME='RM_IHPSG13_1P_64x64_c2_bm_bist'
def prepare(out, ring=False):
    pdk=ROOT/'build/deps/v2-pdk';tt=ROOT/'build/deps/v2-tt'
    verify_checkout(pdk,PDK_REV);verify_checkout(tt,TT_REV)
    out.mkdir(parents=True,exist_ok=False)
    shutil.copy2(Path(__file__).with_name('sram_probe.v'),out/'sram_probe.v')
    macro_root=f'/pdk/ihp-sg13g2/libs.ref/sg13g2_sram'
    source=(pdk/'ihp-sg13g2/libs.ref/sg13g2_sram/verilog'/f'{NAME}.v').read_text()
    header=source[source.index('module '+NAME):source.index('// ---- Simulation-only')]
    (out/'macro.vh').write_text('(* blackbox *) '+header+'\nendmodule\n')
    config=json.loads((ROOT/'src/config.json').read_text())
    config.update({
        'DESIGN_NAME':'tt_um_mcranny_sram_probe','VERILOG_FILES':['dir::sram_probe.v'],
        'DIE_AREA':[0,0,1289.28,710.64],
        'FP_DEF_TEMPLATE':'/tt/tech/ihp-sg13cmos5l/def/tt_block_6x4_pgvdd.def',
        'VDD_PIN':'VPWR','GND_PIN':'VGND','RT_MIN_LAYER':'Metal2','RT_MAX_LAYER':'Metal4',
        'RT_CLOCK_MIN_LAYER':'Metal2','RT_CLOCK_MAX_LAYER':'Metal4',
        'OPENROAD_THREADS':2,'STA_THREADS':2,
        'MACROS':{NAME:{'gds':[f'{macro_root}/gds/{NAME}.gds'],
            'lef':[f'{macro_root}/lef/{NAME}.lef'],'vh':['dir::macro.vh'],
            'instances':{'sram':{'location':[250,320],'orientation':'N'}},
            'lib':{f'nom_{corner}':[f'{macro_root}/lib/{NAME}_{file_corner}.lib'] for corner,file_corner in [
                ('typ_1p20V_25C','typ_1p20V_25C'),('slow_1p08V_125C','slow_1p08V_125C'),
                ('fast_1p32V_m40C','fast_1p32V_m55C')]}}},
        'PDN_MACRO_CONNECTIONS':['sram VPWR VGND VDD! VSS!','sram VPWR VGND VDDARRAY! VSS!'],
        'PDN_HORIZONTAL_LAYER':'Metal3',
        'RUN_KLAYOUT_DRC':True,
        'MAGIC_EXT_USE_GDS':True,
        'EXTRA_SPICE_MODELS':[f'{macro_root}/cdl/{NAME}.cdl'],
        'MAGIC_GDS_FLATGLOB':['lvsres_*','*_CELL_SUB','VIA_M1_*','VIA_M2_*','RSC_*','*_CELL_CORNER','*_BITKIT_CORNER'],
        'MAGIC_PDK_SETUP':'dir::magic_setup.tcl'
    })
    if ring:
        shutil.copy2(Path(__file__).with_name('pdn_ring.tcl'),out/'pdn_ring.tcl')
        config['PDN_CFG']='dir::pdn_ring.tcl'
    (out/'magic_setup.tcl').write_text('source /pdk/ihp-sg13cmos5l/libs.tech/magic/ihp-sg13cmos5l.tcl\nsource /pdk/ihp-sg13cmos5l/libs.tech/magic/read_sram_gds.tcl\n')
    (out/'config.json').write_text(json.dumps(config,indent=2)+'\n')
    note='Physical integration probe only. Fast SRAM uses provided -55 C characterization with -40 C standard cells. This explicit mixed-temperature diagnostic does not establish V2 fast-corner timing acceptance. No file was relabeled or modified.\n'
    (out/'TIMING-CORNER-NOTE.txt').write_text(note)
    inputs={str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in out.iterdir() if p.is_file()}
    macro_files={str(p.relative_to(pdk)):hashlib.sha256(p.read_bytes()).hexdigest() for p in (pdk/'ihp-sg13g2/libs.ref/sg13g2_sram').glob('*/*') if p.is_file() and p.name.startswith(NAME) and p.suffix in ['.gds','.lef','.lib','.v','.cdl']}
    (out/'manifest.json').write_text(json.dumps({'created_utc':datetime.now(timezone.utc).isoformat(),'image':IMAGE,'pdk':PDK_REV,'tt':TT_REV,'inputs':inputs,'macro_files':macro_files,'timing_limitation':note,'release_acceptance':False},indent=2)+'\n')
    return out
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);parser.add_argument('--ring',action='store_true');args=parser.parse_args();print(prepare(args.output.resolve(), args.ring))
