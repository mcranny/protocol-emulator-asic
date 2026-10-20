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
