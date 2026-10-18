"""Synthetic physical artifacts for negative acceptance tests."""

from tools import check_physical as gate


def build_physical_evidence(tmp_path):
    metrics = {
        "design__instance_unmapped__count": 0,
        "route__drc_errors": 0,
        "magic__drc_error__count": 0,
        "design__lvs_error__count": 0,
        "antenna__violating__nets": 0,
        "antenna__violating__pins": 0,
        "route__antenna_violation__count": 0,
        "design__instance__area__stdcell": 300000,
        "design__core__area": 900000,
        "design__instance__utilization__stdcell": 1 / 3,
    }
    for corner in gate.CORNERS:
        metrics[f"timing__unannotated_net_filtered__count__corner:{corner}"] = 0
        for kind in ("setup", "hold"):
            metrics[f"timing__{kind}__ws__corner:{corner}"] = 1.0
            metrics[f"timing__{kind}_r2r__ws__corner:{corner}"] = 2.0
            metrics[f"timing__{kind}_vio__count__corner:{corner}"] = 0
            metrics[f"timing__{kind}_r2r_vio__count__corner:{corner}"] = 0
    for rule in gate.ELECTRICAL_RULES:
        key = f"design__max_{rule}_violation__count"
        metrics[key] = 0
        for corner in gate.CORNERS:
            metrics[f"{key}__corner:{corner}"] = 0
    config = {
        "CLOCK_PERIOD": 40,
        "STA_CORNERS": list(gate.CORNERS),
        "DIE_AREA": gate.DIE_AREA.copy(),
        "MAX_TRANSITION_CONSTRAINT": 1.5,
    }
    pre = tmp_path / "precheck.xml"
    pre.write_text("<testsuite>" + "<testcase/>" * 9 + "</testsuite>")
    gl = tmp_path / "gatelevel.xml"
    gl.write_text(
        "<testsuite>"
        + "".join(f'<testcase name="{name}"/>' for name in sorted(gate.PIN_SUITES))
        + "</testsuite>"
    )
    return metrics, config, pre, gl


def reset_evidence(identity, versions=(1, 2), netlist_hash=None):
    """Complete synthetic audit for acceptance-failure fixtures."""
    return dict(
        identity,
        passed=True,
        simulation="gatelevel" if netlist_hash else "rtl",
        netlist_sha256=netlist_hash,
        commands=[{"exit_code": 0} for _ in versions],
        reports={
            f"v{v}": {
                "version": v,
                "passed": True,
                "simulator": "Icarus Verilog",
                "resolve_x": "error",
                "startup_unknown": "X",
                "replacement_unknown": "X",
                "replacement_unknown_before_reset": "X",
                "checks": [
                    "startup_reset_reload",
                    "reset_during_activity",
                    "powered_disable_and_reselection",
                    "state_loss_reset_reconfigure_reload",
                ],
            }
            for v in versions
        },
    )


def initialization_evidence(inputs):
    memories = {
        "protocol_engine": [("fifo", 32), ("program_mem", 1536)],
        "protocol_engine_v2": [("program_mem", 1536), ("rx_mem", 256), ("tx_mem", 128)],
        "protocol_capture_v2": [("records", 2048)],
        "protocol_input_filter_v2": [],
    }
    bookkeeping = {
        "protocol_engine": "past_valid",
        "protocol_engine_v2": "past_valid",
        "protocol_capture_v2": "past_valid",
        "protocol_input_filter_v2": "valid",
        "tt_um_mcranny_protocol_emulator_v2": "formal_past_valid",
    }
    v2 = [
        "protocol_engine_v2",
        "protocol_capture_v2",
        "protocol_input_filter_v2",
        "tt_um_mcranny_protocol_emulator_v2",
    ]
    scopes = {
        "rtl-tt_um_mcranny_protocol_emulator": ["protocol_engine"],
        "rtl-tt_um_mcranny_protocol_emulator_v2": v2,
        **{"formal-" + name: [name] for name in memories},
        "formal-tt_um_mcranny_protocol_emulator_v2": v2,
    }
    return {
        "passed": True,
        "sources": {
            k: v for k, v in inputs.items() if k.startswith("src/") and k.endswith(".v")
        },
        "results": {
            name: {
                "formal_bookkeeping": [
                    {"module": module, "name": bookkeeping[module], "value": "0"}
                    for module in modules
                ]
                if name.startswith("formal-")
                else [],
                "uninitialized_memories": [
                    {"module": module, "name": memory, "bits": bits}
                    for module in modules
                    for memory, bits in memories.get(module, [])
                ],
            }
            for name, modules in scopes.items()
        },
    }
