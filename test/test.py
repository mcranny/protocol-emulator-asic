"""Pin-driven host protocol and independent UART acceptance tests.

The same tests run on RTL and the gate-level netlist; no internal RTL handles
are used here. Cycle-level engine comparisons live in test_engine.py.
"""
import json
from pathlib import Path
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from protocol_emulator.isa import assemble, encode
from protocol_emulator.firmware import uart_program


class Host:
    def __init__(self, dut, half_ns=500):
        self.dut = dut
        self.half = half_ns

    async def exchange(self, cmd=0, addr=0, payload=0, bits=40):
        d = self.dut
        word = cmd << 32 | addr << 24 | payload
        d.ui_in.value = 2
        await Timer(250, unit="ns")
        d.ui_in.value = 0
        await Timer(250, unit="ns")
        response = 0
        for index in range(bits):
            bit = (word >> (39 - index)) & 1 if index < 40 else 0
            d.ui_in.value = bit << 2
            await Timer(self.half, unit="ns")
            d.ui_in.value = bit << 2 | 1
            response = response << 1 | (int(d.uo_out.value) & 1)
            await Timer(self.half, unit="ns")
            d.ui_in.value = bit << 2
        await Timer(250, unit="ns")
        d.ui_in.value = 2
        await Timer(250, unit="ns")
        return response

    async def request(self, cmd, addr=0, payload=0, reject=False):
        await self.exchange(cmd, addr, payload)
        response = await self.exchange()
        assert response >> 32 == cmd | (0x80 if reject else 0), hex(response)
        assert response >> 24 & 255 == addr
        return response & 0xFFFFFF

    async def load(self, words):
        for addr, word in enumerate(words):
            assert await self.request(1, addr, word) == word
        for addr, word in enumerate(words):
            assert await self.request(2, addr) == word


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 2
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


async def capture(dut, wave):
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        oe = int(dut.uio_oe.value) & 1
        value = int(dut.uio_out.value) & 1 if oe else 1
        wave.append((value, oe))


def decode_uart(wave, expected, period):
    """Compare complete bit cells and decode bytes independently of the ISA."""
    starts, cursor = [], 1
    for byte in expected:
        while cursor < len(wave) and not (wave[cursor - 1][0] == 1 and wave[cursor][0] == 0):
            cursor += 1
        assert cursor + 10 * period <= len(wave), "missing or truncated UART frame"
        starts.append(cursor)
        bits = []
        for bit in range(10):
            cell = wave[cursor + bit * period:cursor + (bit + 1) * period]
            value = cell[period // 2][0]
            assert all(pair == (value, 1) for pair in cell), ("unstable bit cell", cursor, bit)
            bits.append(value)
        assert bits[0] == 0 and bits[9] == 1
        assert sum(bits[bit + 1] << bit for bit in range(8)) == byte
        cursor += 10 * period
    assert all(b - a == 10 * period for a, b in zip(starts, starts[1:])), starts
    return starts


@cocotb.test()
async def host_protocol_and_safety(dut):
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
    for cmd, addr, payload in ((2, 64, 0), (1, 64, 0), (10, 0, 0),
                               (7, 0, 256), (1, 0, 0xF00000), (2, 63, 0)):
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
async def uart_firmware_acceptance(dut):
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
async def host_phase_and_interrupted_uart(dut):
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
