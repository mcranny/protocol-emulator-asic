"""Exercise the isolated wrapper against the pinned SRAM behavioral model."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_all_macro_words_and_bytes(tmp_path):
    models = ROOT / 'build/deps/v2-pdk/ihp-sg13g2/libs.ref/sg13g2_sram/verilog'
    if not models.exists() or not shutil.which('iverilog') or not shutil.which('vvp'):
        pytest.skip('requires pinned PDK checkout and Icarus Verilog')
    executable = tmp_path / 'probe.vvp'
    subprocess.run([
        'iverilog', '-g2012', '-DFUNCTIONAL', '-s', 'tb', '-o', str(executable),
        str(ROOT / 'physical/sram_probe/sram_probe.v'),
        str(ROOT / 'tests/sram_probe/tb.v'),
        str(models / 'RM_IHPSG13_1P_64x64_c2_bm_bist.v'),
        str(models / 'RM_IHPSG13_1P_core_behavioral_bm_bist.v'),
    ], check=True, capture_output=True, text=True)
    result = subprocess.run(['vvp', '-n', str(executable)], check=True,
                            capture_output=True, text=True, timeout=30)
    assert 'PASS: 64 addresses, all 512 bytes, disabled write, IO enables' in result.stdout
