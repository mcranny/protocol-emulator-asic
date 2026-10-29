"""Negative tests for initialized storage and missing four-state evidence."""

import pytest

from tools.audit_initialization import inspect


@pytest.mark.parametrize(
    "value", ["0", "1"], ids=["initialized-zero", "initialized-one"]
)
def test_synthesized_state_must_not_be_initialized(value):
    data = {
        "modules": {
            "protocol_engine": {
                "netnames": {"running": {"attributes": {"init": value}}}
            }
        }
    }
    with pytest.raises(ValueError, match="initialized state"):
        inspect(data)


@pytest.mark.parametrize(
    "name,value",
    [("running", "0"), ("past_valid", "1")],
    ids=["dut-state", "history-starts-valid"],
)
def test_formal_mode_only_initializes_reset_history(name, value):
    data = {
        "modules": {
            "protocol_engine": {"netnames": {name: {"attributes": {"init": value}}}}
        }
    }
    with pytest.raises(ValueError, match="initialized state"):
        inspect(data, formal=True)


def test_v3_formal_bookkeeping_may_start_at_zero():
    data = {
        "modules": {
            "v3_pin_cell": {"netnames": {"valid": {"attributes": {"init": "0"}}}},
            "v3_engine": {"netnames": {"f_past_valid": {"attributes": {"init": "0"}}}},
            "v3_host_spi": {"netnames": {"started": {"attributes": {"init": "0"}}}},
            "v3_capture": {"netnames": {"past_valid": {"attributes": {"init": "0"}}}},
            "v3_input_filter": {"netnames": {"valid": {"attributes": {"init": "0"}}}},
        }
    }
    result = inspect(data, formal=True)
    assert {item["module"] for item in result["formal_bookkeeping"]} == set(data["modules"])


def test_a_parametrized_fifo_keeps_its_bookkeeping_name():
    data = {
        "modules": {
            r"$paramod$abc\v3_fifo": {
                "netnames": {"started": {"attributes": {"init": "0"}}}
            }
        }
    }
    result = inspect(data, formal=True)
    assert result["formal_bookkeeping"][0]["module"].endswith("v3_fifo")


def test_a_capture_parameter_suffix_keeps_its_bookkeeping_name():
    data = {
        "modules": {
            r"$paramod\v3_capture\DEPTH=s32'100000": {
                "netnames": {"past_valid": {"attributes": {"init": "0"}}}
            }
        }
    }
    result = inspect(data, formal=True)
    assert result["formal_bookkeeping"][0]["name"] == "past_valid"


def test_host_chip_select_history_starts_high():
    data = {
        "modules": {
            "v3_host_spi": {"netnames": {"cs_q": {"attributes": {"init": "1"}}}}
        }
    }
    result = inspect(data, formal=True)
    assert result["formal_bookkeeping"] == [
        {"module": "v3_host_spi", "name": "cs_q", "value": "1"}
    ]
    data["modules"]["v3_host_spi"]["netnames"]["cs_q"]["attributes"]["init"] = "0"
    with pytest.raises(ValueError, match="initialized state"):
        inspect(data, formal=True)


def test_v3_formal_bookkeeping_rejects_a_real_register():
    data = {
        "modules": {
            "v3_engine": {"netnames": {"running_r": {"attributes": {"init": "0"}}}}
        }
    }
    with pytest.raises(ValueError, match="initialized state"):
        inspect(data, formal=True)


def test_formal_history_does_not_initialize_memory():
    data = {
        "modules": {
            "protocol_engine": {
                "netnames": {"past_valid": {"attributes": {"init": "0"}}},
                "cells": {
                    "program_mem": {"type": "$mem_v2", "parameters": {"INIT": "xxxx"}}
                },
            }
        }
    }
    result = inspect(data, formal=True)
    assert result["uninitialized_memories"] == [
        {"module": "protocol_engine", "name": "program_mem", "bits": 4}
    ]
    data["modules"]["protocol_engine"]["cells"]["program_mem"]["parameters"]["INIT"] = (
        "xxx0"
    )
    with pytest.raises(ValueError, match="not unconstrained"):
        inspect(data, formal=True)
