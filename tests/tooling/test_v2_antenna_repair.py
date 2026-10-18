"""Antenna continuation preserves the fixed constraints and enters repair before routing."""

import hashlib
from types import SimpleNamespace

import pytest

from tools import v2_route_flow as flow
from tools.v2_antenna_connectivity import audit
from tools.v2_antenna_repair import relocate, repair_config
from tools.v2_closure import FIXED


def test_repair_changes_only_diode_strategy():
    original = dict(FIXED, DRT_ANTENNA_REPAIR_JUMPER_ONLY=True, FP_PDN_VPITCH=50)
    config = repair_config(original)
    assert config["DRT_ANTENNA_REPAIR_DIODE_ONLY"] is True
    assert config["DRT_ANTENNA_REPAIR_JUMPER_ONLY"] is False
    assert config["FP_PDN_VPITCH"] == 50
    assert original["DRT_ANTENNA_REPAIR_JUMPER_ONLY"] is True
    for key, value in FIXED.items():
        assert config[key] == (6 if key.endswith("_THREADS") else value)
    with pytest.raises(ValueError, match="RT_MIN_LAYER"):
        repair_config(dict(original, RT_MIN_LAYER="Metal1"))


def test_checkpoint_keeps_earlier_predecessor_mount():
    assert relocate({"odb": "/closure/x.odb", "nl": "/previous/y.v"}) == {
        "odb": "/routed/x.odb",
        "nl": "/previous/y.v",
    }


def test_pinned_script_enters_antenna_loop_without_initial_reroute(monkeypatch):
    original = (
        "lappend drt_args -or_seed 42\ndrt_run $i {*}$drt_args\n\nincr i\n"
        "set diodes_inserted [log_cmd repair_antennas {*}$arg_list]\n"
        "drt_run $i {*}$drt_args\n"
    )
    monkeypatch.setattr(
        flow, "PINNED_DRT_SHA256", hashlib.sha256(original.encode()).hexdigest()
    )
    result = flow.antenna_script(original, 43, 1)
    assert result.count("drt_run $i") == 1
    assert result.index("repair_antennas") < result.index("drt_run $i")
    assert "log_cmd check_antennas" in result
    assert result.index("set_power_nets.tcl") < result.index("set_global_connections")
    assert result.index("set_global_connections") < result.index("drt_run $i")
    with pytest.raises(ValueError, match="pinned"):
        flow.antenna_script(original + "# drift", 43, 1)


def block(nets):
    pins = [
        SimpleNamespace(
            getMTerm=lambda key=key: SimpleNamespace(getName=lambda: key),
            getNet=lambda net=net: SimpleNamespace(getName=lambda: net)
            if net
            else None,
        )
        for key, net in nets.items()
    ]
    inst = SimpleNamespace(
        getName=lambda: "diode1",
        getITerms=lambda: pins,
        getMaster=lambda: SimpleNamespace(getName=lambda: "sg13cmos5l_antennanp"),
    )
    return SimpleNamespace(getInsts=lambda: [inst])


def test_diode_audit_requires_signal_and_both_supplies():
    assert audit(block({"A": "signal", "VDD": "VPWR", "VSS": "VGND"}))["passed"]
    for pin in ("A", "VDD", "VSS"):
        nets = {"A": "signal", "VDD": "VPWR", "VSS": "VGND"}
        nets[pin] = None
        assert not audit(block(nets))["passed"]
    assert not audit(block({"A": "VGND", "VDD": "VPWR", "VSS": "VGND"}))["passed"]
