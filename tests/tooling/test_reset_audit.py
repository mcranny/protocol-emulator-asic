"""Negative tests for initialized storage and missing four-state evidence."""

import copy

import pytest

from tests.support.evidence import reset_evidence
from tools.audit_initialization import inspect
from tools.validate_reset import check_record, environment


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


@pytest.fixture
def reset_record():
    identity = dict(
        source_commit="a" * 40, source_dirty=False, inputs={"src/top.v": "b" * 64}
    )
    return identity, reset_evidence(identity)


@pytest.mark.parametrize(
    "corruption",
    ["coerce", "no_unknown", "missing_phase", "missing_version", "failed", "stale"],
    ids=[
        "x-coercion",
        "zero-startup",
        "missing-state-loss",
        "missing-v1",
        "failed-simulation",
        "stale-source",
    ],
)
def test_reset_evidence_cannot_omit_required_observations(reset_record, corruption):
    identity, record = reset_record
    record = copy.deepcopy(record)
    data = record["reports"]["v2"]
    if corruption == "coerce":
        data["resolve_x"] = "zeros"
    elif corruption == "no_unknown":
        data["startup_unknown"] = "00000000"
    elif corruption == "missing_phase":
        data["checks"].pop()
    elif corruption == "missing_version":
        del record["reports"]["v1"]
    elif corruption == "failed":
        record["commands"][0]["exit_code"] = 1
    else:
        record["source_commit"] = "c" * 40
    with pytest.raises(ValueError):
        check_record(record, (1, 2), identity, "rtl")


def test_gatelevel_reset_evidence_must_match_netlist(reset_record):
    identity, _ = reset_record
    record = reset_evidence(identity, (2,), "c" * 64)
    check_record(record, (2,), identity, "gatelevel", "c" * 64)
    with pytest.raises(ValueError, match="netlist differs"):
        check_record(record, (2,), identity, "gatelevel", "d" * 64)


def test_audit_ignores_inherited_coercion_filters_and_simulator_overrides(monkeypatch):
    monkeypatch.setenv("COCOTB_RESOLVE_X", "zeros")
    monkeypatch.setenv("COCOTB_TEST_FILTER", "nonexistent")
    monkeypatch.setenv("COMPILE_ARGS", "-DZERO_INITIALIZATION")
    result = environment()
    assert result["COCOTB_RESOLVE_X"] == "error"
    assert "COCOTB_TEST_FILTER" not in result
    assert "COMPILE_ARGS" not in result


@pytest.mark.parametrize(
    "corruption",
    [
        "empty-result",
        "missing-memory",
        "duplicate-memory",
        "wrong-size",
        "missing-history",
        "nonzero-history",
        "unexpected-history",
    ],
    ids=[
        "empty-result",
        "missing-memory",
        "duplicate-memory",
        "wrong-size",
        "missing-history",
        "nonzero-history",
        "unexpected-history",
    ],
)
def test_initialization_summary_requires_complete_inventories(corruption):
    from tests.support.evidence import initialization_evidence
    from tools.validate_reset import check_initialization

    inputs = {
        "src/" + name + ".v": "a" * 64
        for name in (
            "engine",
            "host_spi",
            "project",
            "v2_engine",
            "v2_capture",
            "v2_input_filter",
            "v2_project",
        )
    }
    summary = initialization_evidence(inputs)
    check_initialization(summary, inputs)
    key = "formal-protocol_engine_v2"
    result = summary["results"][key]
    if corruption == "empty-result":
        summary["results"][key] = {}
    elif corruption == "missing-memory":
        result["uninitialized_memories"].pop()
    elif corruption == "duplicate-memory":
        result["uninitialized_memories"].append(result["uninitialized_memories"][0])
    elif corruption == "wrong-size":
        result["uninitialized_memories"][0]["bits"] = 1
    elif corruption == "missing-history":
        result["formal_bookkeeping"] = []
    elif corruption == "nonzero-history":
        result["formal_bookkeeping"][0]["value"] = "1"
    else:
        result["formal_bookkeeping"][0]["name"] = "running"
    with pytest.raises(ValueError, match="inventory differs"):
        check_initialization(summary, inputs)
