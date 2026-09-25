"""Top-level pin-only V2 transport and UART integration checks."""
import json
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer
from cocotb.utils import get_sim_time

from test import Host, reset, capture, decode_uart
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.firmware import uart_rx, uart_tx, spi_target, spi_controller, i2c_controller, i2c_target
from protocol_emulator.v2.peers import I2CTarget
from protocol_emulator.v2.host import command_frame, tx_commands, parse_response


async def batch(host, requests, allow_empty=False):
    requests = list(requests)
    result = []
    previous = None
    for frame in requests + [bytes(5)]:
        response = await host.exchange(frame[0], frame[1], int.from_bytes(frame[2:], "big"))
        if previous is not None:
            result.append(parse_response(previous, response.to_bytes(5, "big"), allow_empty=allow_empty))
        previous = frame
    return result


async def load(host, engine, source):
    words = assemble(source, capacity=64)
    await batch(host, [command_frame(1, engine, i, word) for i, word in enumerate(words)])
    assert await batch(host, [command_frame(2, engine, i) for i in range(len(words))]) == words


async def send_uart(dut, data, period=217):
    dut.uio_in.value = 255
    await Timer(123, unit="ns")
    for value in data:
        for bit in [0] + [value >> i & 1 for i in range(8)] + [1]:
            dut.uio_in.value = 253 | bit << 1
            await Timer(period * 40, unit="ns")
    dut.uio_in.value = 255


@cocotb.test()
async def v2_pin_transport_safety(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    await reset(dut)
    h = Host(dut)
    assert await h.request(9) == 0x020240
    assert await h.request(10) == 0x021010
    await h.request(2, addr=63, reject=True)
    await h.request(5)
    await h.request(0x20, payload=3, reject=True)
    assert int(dut.uo_out.value) & 6 == 0
    await h.request(0x21, payload=1)
    await h.request(0x21, addr=128, payload=1, reject=True)
    await h.request(0x21, addr=128, payload=0x0202)
    await load(h, 0, "DRIVE 1, 1\nJMP 1")
    await load(h, 1, "DRIVE 2, 2\nJMP 1")
    await h.request(0x20, payload=3)
    assert int(dut.uio_oe.value) == 1
    await h.request(0x21, payload=2, reject=True)
    await h.exchange(bits=39)
    await ClockCycles(dut.clk, 10)
    assert int(dut.uio_oe.value) == 0
    for engine in (0, 1):
        await h.request(5, addr=engine << 7)
    # Aborted retrieval must not consume the queued record.
    await load(h, 1, "LI 0, 0\nRX 85\nHALT")
    await h.request(3, addr=128)
    await h.exchange(0x13, 128)
    await h.exchange(bits=12)
    assert await h.request(8, addr=128) >> 5 == 1
    result = await h.request(0x13, addr=128)
    assert result & 65535 == 0x5500
    assert await h.request(8, addr=128) >> 5 == 0


@cocotb.test()
async def v2_full_duplex_host_budget(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    await reset(dut)
    h = Host(dut)
    await h.request(0x21, payload=1)
    await load(h, 0, uart_tx(217))
    await load(h, 1, uart_rx(217))
    expected_tx = list(range(8))
    await batch(h, tx_commands(0, expected_tx))
    wave = []
    monitor = cocotb.start_soon(capture(dut, wave))
    await h.request(0x20, payload=3)
    expected_rx = [(i * 73 + 19) & 255 for i in range(128)]
    sender = cocotb.start_soon(send_uart(dut, expected_rx))
    received, durations = [], []
    cached_level = 8
    # Each service window includes all clocked command/response frames and gaps.
    for _ in range(24):
        begin = round(get_sim_time(unit="ns"))
        count = min(8, 16 - cached_level)
        outgoing = [(len(expected_tx) + i) & 255 for i in range(count)]
        requests = tx_commands(0, outgoing) + [command_frame(0x13, 1)] * 8
        responses = await batch(h, requests, allow_empty=True)
        expected_tx.extend(outgoing)
        tx_frames = len(requests) - 8
        if tx_frames:
            last = responses[tx_frames - 1]
            cached_level = (last & 31) + (last >> 5 & 3)
        for result in responses[tx_frames:]:
            if result is not None:
                received.append((result & 255, result >> 8 & 255))
        duration = round(get_sim_time(unit="ns")) - begin
        durations.append(duration)
        assert duration <= 500_000
        await Timer(500_000 - duration, unit="ns")
    await sender
    received += [(result & 255, result >> 8 & 255) for result in
                 await batch(h, [command_frame(0x13, 1)] * 16, allow_empty=True) if result is not None]
    await ClockCycles(dut.clk, 16 * 2170)
    monitor.cancel()
    assert received == [(value, 0) for value in expected_rx]
    decode_uart(wave, expected_tx, 217)
    assert await h.request(6) == 1
    assert await h.request(6, addr=128) == 1
    Path("output").mkdir(exist_ok=True)
    Path("output/v2-throughput.json").write_text(json.dumps({
        "host_sck_hz": 1000000, "service_interval_ns": 500000,
        "maximum_service_duration_ns": max(durations), "tx_bytes": len(expected_tx),
        "rx_bytes": len(received), "pin_driven": True, "physical_host_validated": False,
    }, indent=2))


@cocotb.test()
async def v2_uart_megabaud_buffered_duplex(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    await reset(dut)
    h = Host(dut)
    await h.request(0x21, payload=1)
    await load(h, 0, uart_tx(25))
    await load(h, 1, uart_rx(25))
    outgoing = [(i * 37) & 255 for i in range(16)]
    incoming = [(i * 73 + 19) & 255 for i in range(16)]
    await batch(h, tx_commands(0, outgoing))
    wave = []
    monitor = cocotb.start_soon(capture(dut, wave))
    async def peer():
        while int(dut.uo_out.value) & 6 != 6:
            await Timer(10, unit="ns")
        await send_uart(dut, incoming, 25)
    sender = cocotb.start_soon(peer())
    await h.request(0x20, payload=3)
    await sender
    await ClockCycles(dut.clk, 260)
    monitor.cancel()
    records = await batch(h, [command_frame(0x13, 1)] * 16)
    assert [value & 65535 for value in records] == incoming
    decode_uart(wave, outgoing, 25)
    assert await h.request(6) == 1
    assert await h.request(6, addr=128) == 1


@cocotb.test()
async def v2_spi_target_independent_peer(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for mode in range(4):
        for phase in (3, 19, 37):
            await reset(dut)
            h = Host(dut)
            cpol, cpha = mode >> 1, mode & 1
            dut.uio_in.value = 4 | cpol << 1
            await h.request(0x21, payload=8)
            await h.request(0x22, payload=0x040404)
            await h.request(0x24, payload=1)
            await load(h, 0, spi_target(mode))
            outgoing = [0xA5, 0x81, 0x5A]
            incoming = [0x55, 0xC3, 0x80]
            await batch(h, tx_commands(0, outgoing))
            await h.request(3)
            dut.uio_in.value = cpol << 1
            await Timer(1000 + phase, unit="ns")
            got = []
            for value in incoming:
                result = 0
                for bit in range(7, -1, -1):
                    mosi = value >> bit & 1
                    if cpha:
                        dut.uio_in.value = (1 - cpol) << 1 | mosi
                        await Timer(500, unit="ns")
                        dut.uio_in.value = cpol << 1 | mosi
                        result = result << 1 | (int(dut.uio_out.value) >> 3 & 1)
                        await Timer(500, unit="ns")
                    else:
                        dut.uio_in.value = cpol << 1 | mosi
                        await Timer(500, unit="ns")
                        dut.uio_in.value = (1 - cpol) << 1 | mosi
                        result = result << 1 | (int(dut.uio_out.value) >> 3 & 1)
                        await Timer(500, unit="ns")
                if not cpha:
                    dut.uio_in.value = cpol << 1 | mosi
                got.append(result)
            await Timer(500, unit="ns")
            dut.uio_in.value = 4 | cpol << 1
            await Timer(1000, unit="ns")
            assert got == outgoing, (mode, phase, got)
            records = await batch(h, [command_frame(0x13)] * 3)
            assert [value & 65535 for value in records] == incoming
            assert await h.request(6) == 1
            assert int(dut.uio_oe.value) == 0


@cocotb.test()
async def v2_spi_controller_independent_decoder(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for mode in range(4):
        await reset(dut)
        h = Host(dut)
        await h.request(0x21, payload=7)
        await load(h, 0, spi_controller(mode))
        outgoing = [0xA5, 0x81, 0x5A]
        await batch(h, tx_commands(0, outgoing))
        dut.uio_in.value = 8
        bits = []
        async def decode_edges():
            previous = (mode >> 1) << 1 | 4
            while True:
                await Timer(10, unit="ns")
                current = int(dut.uio_out.value)
                enabled = int(dut.uio_oe.value)
                if enabled & 7 == 7 and not current & 4 and (current ^ previous) & 2:
                    leading = (current >> 1 & 1) != mode >> 1
                    if leading != bool(mode & 1):
                        bits.append(current & 1)
                previous = current
        observer = cocotb.start_soon(decode_edges())
        await h.request(3)
        await Timer(50000, unit="ns")
        observer.cancel()
        assert bits == [value >> bit & 1 for value in outgoing for bit in range(7, -1, -1)]
        records = await batch(h, [command_frame(0x13)] * 3)
        assert [value & 65535 for value in records] == [255] * 3
        assert await h.request(6) == 1


@cocotb.test()
async def v2_i2c_controller_independent_peer(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for writes, reads in ((2, 0), (0, 3), (2, 3)):
        await reset(dut)
        h = Host(dut)
        await h.request(0x21, payload=0x0303)
        await load(h, 0, i2c_controller(write_count=writes, read_count=reads))
        await batch(h, tx_commands(0, [0x55, 0xA5][:writes]))
        peer = I2CTarget(read_data=bytes([0x81, 0x55, 0xA5]))
        async def resolve_bus():
            while True:
                await Timer(10, unit="ns")
                value, enable = int(dut.uio_out.value), int(dut.uio_oe.value)
                bus = (value | ~enable) & ~peer.drive_low & 3
                peer.observe(bus)
                dut.uio_in.value = 252 | ((value | ~enable) & ~peer.drive_low & 3)
        resolver = cocotb.start_soon(resolve_bus())
        await h.request(3)
        await Timer(250000, unit="ns")
        assert await h.request(6) == 0
        assert peer.received == [0x55, 0xA5][:writes]
        assert peer.starts == (2 if writes and reads else 1)
        assert peer.stops == 1
        if reads:
            records = await batch(h, [command_frame(0x13)] * reads)
            assert [value & 65535 for value in records] == [0x81, 0x55, 0xA5][:reads]
            assert peer.acks == [0] * (reads - 1) + [1]
        resolver.cancel()


@cocotb.test()
async def v2_i2c_target_independent_peer(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for low_ns, high_ns in ((1300, 1200), (5000, 5000)):
        await reset(dut)
        h = Host(dut)
        await h.request(0x21, payload=0x0303)
        await h.request(0x22, payload=0x010203)
        await h.request(0x23, payload=0x010303)
        await h.request(0x24, payload=3)
        await load(h, 0, i2c_target())
        await batch(h, tx_commands(0, [0xA5, 0x81]))
        master = [3]
        bus = [3]
        async def resolve_bus():
            while True:
                value, enable = int(dut.uio_out.value), int(dut.uio_oe.value)
                bus[0] = master[0] & (value | ~enable) & 3
                dut.uio_in.value = 252 | bus[0]
                await Timer(10, unit="ns")
        resolver = cocotb.start_soon(resolve_bus())
        await h.request(3)
        async def drive(value, duration):
            master[0] = value
            await Timer(duration, unit="ns")
        async def high(sda):
            master[0] = 2 | sda
            for _ in range(1000):
                await Timer(10, unit="ns")
                if bus[0] & 2:
                    break
            else:
                raise AssertionError("target exceeded stretching budget")
            await Timer(high_ns - 10, unit="ns")
            return bus[0] & 1
        async def start():
            await drive(3, 1500)
            await drive(2, 1500)
            await drive(0, low_ns)
        async def write(value):
            for bit in range(7, -1, -1):
                sda = value >> bit & 1
                await drive(sda, low_ns)
                await high(sda)
                master[0] = sda
            await drive(1, low_ns)
            ack = await high(1)
            await drive(1, low_ns)
            assert ack == 0
        async def read(ack):
            value = 0
            for _ in range(8):
                await drive(1, low_ns)
                value = value << 1 | await high(1)
                master[0] = 1
            await drive(0 if ack else 1, low_ns)
            await high(0 if ack else 1)
            await drive(1, low_ns)
            return value
        await start()
        await write(0x84)
        await write(0x55)
        await start()
        await write(0x85)
        assert await read(True) == 0xA5
        assert await read(False) == 0x81
        await drive(0, low_ns)
        await high(0)
        await drive(3, 2000)
        assert await h.request(0x13) & 65535 == 0x55
        assert await h.request(6) == 1
        assert int(dut.uio_oe.value) == 0
        resolver.cancel()
