"""Four-state pin checks with a fresh DUT standing in for lost powered state."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, Timer
from protocol_emulator.isa import assemble as assemble_v1
from protocol_emulator.v2.isa import assemble as assemble_v2

from support.pins import Host


def ports(top, name):
    return SimpleNamespace(
        **{
            key: getattr(top, name + "_" + key)
            for key in (
                "clk",
                "rst_n",
                "ena",
                "ui_in",
                "uio_in",
                "uo_out",
                "uio_out",
                "uio_oe",
            )
        }
    )


def known(handle, expected=None):
    value = handle.value
    assert value.is_resolvable, (
        f"unknown externally observed value: {handle._name}={value}"
    )
    number = int(value)
    if expected is not None:
        assert number == expected, f"{handle._name}: {number} != {expected}"
    return number


def unknown(handle):
    value = handle.value
    assert not value.is_resolvable, (
        f"startup state unexpectedly resolved: {handle._name}={value}"
    )
    try:
        int(value)
    except ValueError:
        return str(value)
    raise AssertionError("X-to-integer coercion is enabled")


async def settle_cycles(dut, count=10):
    await ClockCycles(dut.clk, count)
    await Timer(1, unit="ns")


async def reset(dut):
    # Exercise asynchronous assertion away from a system-clock edge.
    await FallingEdge(dut.clk)
    await Timer(3, unit="ns")
    dut.rst_n.value = 0
    await Timer(1, unit="ns")
    known(dut.uio_oe, 0)
    await settle_cycles(dut)
    for handle in (dut.uo_out, dut.uio_out, dut.uio_oe):
        known(handle, 0)
    dut.ui_in.value = 2
    dut.rst_n.value = 1
    await settle_cycles(dut)
    for handle in (dut.uo_out, dut.uio_out, dut.uio_oe):
        known(handle, 0)


async def empty_state(host, dut, version):
    # Unwritten memory may remain X; none may leak through a successful reply.
    assert await host.request(9) == (0x010140 if version == 1 else 0x020240)
    for engine in range(version):
        address = engine << 7
        await host.request(2, addr=address + 63, reject=True)
        await host.request(3, addr=address, reject=True)
        await host.request(5, addr=address)
        assert await host.request(8, addr=address) == 0
    if version == 2:
        assert await host.request(0x26) == 0
        assert await host.request(0x32) == 0
        for address in (0, 31):
            for command in (0x35, 0x36, 0x37):
                assert await host.request(command, addr=address) == 0
        await host.request(0x13, reject=True)
        await host.request(5)
    known(dut.uio_oe, 0)


async def drive(host, dut, version, pin):
    mask = 1 << pin
    if version == 1:
        words = assemble_v1(f"SET {mask}, {mask}\nOE {mask}, {mask}\nJMP 2")
    else:
        await host.request(0x21, payload=mask)
        words = assemble_v2(f"DRIVE {mask}, {mask}\nJMP 1")
    await host.load(words)
    await host.request(3)
    known(dut.uio_oe, mask)
    assert known(dut.uio_out) & mask == mask


async def exercise(top, version):
    assert os.environ.get("COCOTB_RESOLVE_X") == "error"
    warm, cold = ports(top, "warm"), ports(top, "cold")
    top.select_cold.value = 0
    for dut in (warm, cold):
        dut.clk.value = 0
        dut.rst_n.value = 1
        dut.ena.value = 1
        dut.ui_in.value = 2
        dut.uio_in.value = 255
    await Timer(5, unit="ns")
    evidence = {
        "version": version,
        "simulator": cocotb.SIM_NAME,
        "resolve_x": "error",
        "startup_unknown": unknown(warm.uo_out),
        "replacement_unknown": unknown(cold.uo_out),
        "checks": [],
    }
    cocotb.start_soon(Clock(warm.clk, 40, unit="ns").start())
    await reset(warm)
    host = Host(warm)
    await empty_state(host, warm, version)
    await drive(host, warm, version, 0)
    evidence["checks"].append("startup_reset_reload")

    # Interrupt both a driving engine and an incomplete host frame.
    warm.ui_in.value = 0
    for bit in range(7):
        warm.ui_in.value = (bit & 1) << 2
        await Timer(250, unit="ns")
        warm.ui_in.value = ((bit & 1) << 2) | 1
        await Timer(250, unit="ns")
    await reset(warm)
    await empty_state(host, warm, version)
    await drive(host, warm, version, 1)
    evidence["checks"].append("reset_during_activity")

    # Powered logical disable only: distinguish this from loss of all state.
    warm.ena.value = 0
    await Timer(1, unit="ns")
    known(warm.uio_oe, 0)
    await settle_cycles(warm)
    warm.ena.value = 1
    await settle_cycles(warm)
    known(warm.uio_oe, 0)
    await host.request(3)
    known(warm.uio_oe, 2)  # program remains valid during powered logical disable
    evidence["checks"].append("powered_disable_and_reselection")

    # The unclocked replacement contains no state from the active instance.
    # Selecting it models power loss, not an RTL implementation of power gating.
    warm.ena.value = 0
    cold.ena.value = 0
    await Timer(1, unit="ns")
    known(warm.uio_oe, 0)
    known(cold.uio_oe, 0)
    evidence["replacement_unknown_before_reset"] = unknown(cold.uo_out)
    top.select_cold.value = 1
    await Timer(1, unit="ns")
    known(top.selected_uio_oe, 0)
    cocotb.start_soon(Clock(cold.clk, 40, unit="ns").start())
    await reset(cold)
    cold.ena.value = 1
    await settle_cycles(cold)
    host = Host(cold)
    await empty_state(host, cold, version)
    await drive(host, cold, version, 2)
    known(top.selected_uio_oe, 4)
    evidence["checks"].append("state_loss_reset_reconfigure_reload")
    evidence["passed"] = True
    Path(os.environ["RESET_AUDIT_REPORT"]).write_text(
        json.dumps(evidence, indent=2) + "\n"
    )
