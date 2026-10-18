"""Pin-driven host protocol and independent UART acceptance tests.

The same tests run on RTL and the gate-level netlist; no internal RTL handles
are used here. Cycle-level engine comparisons live in test_engine.py.
"""

import json
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from protocol_emulator.firmware import uart_program
from protocol_emulator.isa import assemble, encode
from support.pins import Host, capture, decode_uart, reset


@cocotb.test()
async def test_host_protocol_and_safety(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    await reset(dut)
    host = Host(dut)
    assert await host.request(9) == 0x010140
    assert await host.request(6) == 6
    words = assemble("SET 1, 1\nOE 1, 1\nJMP 2")
    await host.load(words)
    await host.request(3)
    assert int(dut.uio_oe.value) == 1
    await host.request(1, 0, encode("NOP"), reject=True)
    assert await host.request(2, 0) == words[0]
    assert await host.request(6) & 0x41 == 0x41
    await host.request(4)
    assert int(dut.uio_oe.value) == 0
    await host.request(5)
    for byte in (0, 85, 165, 255):
        await host.request(7, payload=byte)
    assert await host.request(8) == 4
    await host.request(7, payload=1, reject=True)
    assert await host.request(6) & 0x200
    for cmd, addr, payload in (
        (2, 64, 0),
        (1, 64, 0),
        (10, 0, 0),
        (7, 0, 256),
        (1, 0, 0xF00000),
        (2, 63, 0),
    ):
        await host.request(cmd, addr, payload, reject=True)
        assert int(dut.uio_oe.value) == 0
    for bits in (0, 1, 39, 41, 48):
        await host.request(5)
        await host.exchange(1, 0, encode("HALT"), bits=bits)
        assert await host.request(6) & 0x100
        assert await host.request(2, 0) == words[0]
    # Disable and reset release immediately, before the next system edge.
    await host.request(3)
    dut.ena.value = 0
    await Timer(5, unit="ns")
    assert int(dut.uio_oe.value) == 0
    await ClockCycles(dut.clk, 5)
    dut.ena.value = 1
    await ClockCycles(dut.clk, 5)
    assert int(dut.uio_oe.value) == 0
    await host.request(3)
    dut.rst_n.value = 0
    await Timer(5, unit="ns")
    assert int(dut.uio_oe.value) == 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 5)
    await host.request(3, reject=True)


@cocotb.test()
async def test_uart_firmware_acceptance(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for period in (25, 217):
        await reset(dut)
        host = Host(dut)
        words = uart_program(period)
        await host.load(words)
        data = [0, 0x55, 0xA5, 0xFF]
        for byte in data:
            await host.request(7, payload=byte)
        wave = []
        monitor = cocotb.start_soon(capture(dut, wave))
        await host.request(3)
        # Repeated host reads overlap protocol output. They must not stretch bits.
        for _ in range(6):
            await host.request(6)
        await ClockCycles(dut.clk, 4 * 10 * period + 10)
        monitor.cancel()
        output = Path("output")
        output.mkdir(exist_ok=True)
        evidence = {"period": period, "bytes": data, "firmware": words, "wave": wave}
        (output / f"uart-{period}.json").write_text(json.dumps(evidence))
        decode_uart(wave, data, period)
        assert int(dut.uio_out.value) & 1
        assert int(dut.uio_oe.value) & 1
        # A later FIFO byte resumes correctly after an arbitrarily long idle.
        wave = []
        monitor = cocotb.start_soon(capture(dut, wave))
        await host.request(7, payload=0x5A)
        await ClockCycles(dut.clk, 10 * period + 10)
        monitor.cancel()
        decode_uart(wave, [0x5A], period)
        await host.request(4)
        assert int(dut.uio_oe.value) == 0
        assert not (await host.request(6) & 0x7F0)


@cocotb.test()
async def test_host_phase_and_interrupted_uart(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for phase in (0, 7, 19, 39):
        await reset(dut)
        if phase:
            await Timer(phase, unit="ns")
        host = Host(dut)
        await host.load(assemble("LI 1, 0xA55A\nHALT"))
        assert await host.request(9) == 0x010140
    # At 115200 baud a host STOP frame can complete within the first UART byte.
    for action in ("stop", "reset", "disable"):
        await reset(dut)
        host = Host(dut)
        await host.load(uart_program(217))
        await host.request(7, payload=0)
        await host.exchange(3)
        await ClockCycles(dut.clk, 50)
        await Timer(1, unit="ns")
        assert int(dut.uio_oe.value) & 1
        assert not (int(dut.uio_out.value) & 1)
        if action == "stop":
            await host.exchange(4)
        elif action == "reset":
            dut.rst_n.value = 0
        else:
            dut.ena.value = 0
        await Timer(5, unit="ns")
        assert int(dut.uio_oe.value) == 0
        await ClockCycles(dut.clk, 20)
        assert int(dut.uio_oe.value) == 0
