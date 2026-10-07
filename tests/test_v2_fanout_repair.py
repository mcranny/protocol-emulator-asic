"""The diode ECO cannot bypass antenna, electrical or full-layout checks."""
import pytest
from tools.v2_fanout_repair import baseline_issues, eco_script
from tools import v2_fanout_repair as repair


def test_only_measured_fanout_failure_is_eligible(monkeypatch):
    failures = [f'design__max_fanout_violation__count__corner:{c} = 1' for c in repair.closure.CORNERS]
    monkeypatch.setattr(repair, 'extracted_issues', lambda _: failures)
    baseline_issues({})
    failures.append('route__drc_errors = 1')
    with pytest.raises(ValueError, match='only one fanout'):
        baseline_issues({})


def test_diode_removal_must_recheck_unfiltered_whole_layout():
    script = eco_script()
    assert 'sg13cmos5l_antennanp' in script and '$loads != 9' in script
    assert script.index('dbInst_destroy') < script.index('check_antennas') < script.index('drt::check_drc')
    assert '-net ' not in script and '-box' not in script
    assert 'file size $report] != 0' in script
    assert script.index('error "ECO whole-layout') < script.index('metric_integer route__drc_errors 0')
    assert 'set_max_fanout' not in script
    assert 'drt::step_dr 7 0 1 1 1 100 0.95 3 false' in script
    assert 'drt::step_dr 9 0 8 8 8 100 0.95 0 false' in script
    assert script.count('check_antennas') == 2
