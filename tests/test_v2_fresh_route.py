"""Fresh RTL trials preserve the qualified footprint, timing and routing layers."""
import json
from pathlib import Path

import pytest

from tools.v2_fresh_route import candidate_config, retained_state

ROOT = Path(__file__).resolve().parents[1]


def inputs():
    base = json.loads((ROOT / 'src/config.json').read_text())
    overrides = json.loads((ROOT / 'physical/v2/overrides.json').read_text())
    project = dict(language='Verilog', clock_hz=25_000_000, tiles='6x4',
                   top_module='tt_um_mcranny_protocol_emulator_v2',
                   source_files=['host_spi.v', 'v2_engine.v', 'v2_capture.v',
                                 'v2_input_filter.v', 'v2_project.v'])
    return base, overrides, project, {'6x4': [0, 0, 1289.28, 710.64]}


def test_fresh_trial_overrides_old_metal1_experiment_without_changing_pdn():
    base, overrides, project, sizes = inputs()
    overrides['RT_MIN_LAYER'] = 'Metal1'
    config = candidate_config(base, overrides, project, sizes, 6)
    assert config['RT_MIN_LAYER'] == config['RT_CLOCK_MIN_LAYER'] == 'Metal2'
    assert config['RT_MAX_LAYER'] == config['RT_CLOCK_MAX_LAYER'] == 'Metal4'
    assert config['DIE_AREA'] == sizes['6x4']
    assert config['CLOCK_PERIOD'] == 40
    assert config['MAX_TRANSITION_CONSTRAINT'] == 1.5
    assert config['CLOCK_UNCERTAINTY_CONSTRAINT'] == 0.25
    assert config['PL_RESIZER_HOLD_SLACK_MARGIN'] == 0.05
    assert config['GRT_RESIZER_HOLD_SLACK_MARGIN'] == 0.05
    assert config['OPENROAD_THREADS'] == config['STA_THREADS'] == 6
    assert {k: v for k, v in config.items() if 'PDN' in k} == {
        k: v for k, v in base.items() if 'PDN' in k}
    assert config['VERILOG_FILES'] == ['/closure/src/' + n for n in project['source_files']]
    assert overrides['RT_MIN_LAYER'] == 'Metal1'


def test_fresh_trial_rejects_released_v1_top():
    base, overrides, project, sizes = inputs()
    project['top_module'] = 'tt_um_mcranny_protocol_emulator'
    with pytest.raises(ValueError, match='V2 integration'):
        candidate_config(base, overrides, project, sizes, 6)


def test_retained_checkpoint_relocates_nested_views_without_touching_pdk():
    state = {'odb': '/closure/runs/a.odb', 'sdf': {'fast': '/closure/runs/a.sdf'},
             'libs': ['/pdk/fast.lib'], 'metrics': {}, 'spef': None}
    assert retained_state(state) == {
        'odb': '/previous/runs/a.odb', 'sdf': {'fast': '/previous/runs/a.sdf'},
        'libs': ['/pdk/fast.lib'], 'metrics': {}, 'spef': None}
    assert state['odb'] == '/closure/runs/a.odb'
