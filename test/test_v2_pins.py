"""Top-level pin-only V2 transport and UART integration checks."""
import json
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, RisingEdge, Timer
from cocotb.utils import get_sim_time

from test import Host, reset, capture, decode_uart
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.firmware import uart_rx, uart_tx, spi_target, spi_controller, i2c_controller, i2c_target
from protocol_emulator.v2.peers import I2CTarget
from protocol_emulator.v2.host import command_frame, tx_commands, parse_response
from protocol_emulator.v2.replay import OutputEvent, compile_schedule
from protocol_emulator.v2.scenario import uart_demo, write_artifacts
from protocol_emulator.v2.peers import decode_uart as decode_uart_events


async def capture_words(host):
    status = await host.request(0x32)
    assert not status & 1
    count = status >> 6 & 63
    requests = [command_frame(command, address=i) for i in range(count)
                for command in (0x35, 0x36, 0x37)]
    chunks = []
    for offset in range(0, len(requests), 64):
        chunks += await batch(host, requests[offset:offset + 64])
    return status, [chunks[i] | chunks[i + 1] << 24 | chunks[i + 2] << 48
                    for i in range(0, len(chunks), 3)]


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
    previous_errors = int(dut.uo_out.value) & 8
    # Running-engine readback is busy, including an unwritten valid address.
    # Host addresses must never redirect execution through its shared read port.
    for address in (0, 1, 63, 128, 191):
        await h.request(2, addr=address, reject=True)
        assert int(dut.uo_out.value) & 14 == 6 | previous_errors
        assert int(dut.uio_out.value) == 1 and int(dut.uio_oe.value) == 1
    await h.request(4, addr=128)
    await load(h, 1, "\n".join(f"LI {i % 2}, {i * 1013}" for i in range(63)) + "\nHALT")
    assert await h.request(6) == 1
    assert int(dut.uio_out.value) == 1 and int(dut.uio_oe.value) == 1
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

    # Check pulse widths during all 64 writes and readbacks of the other
    # engine. Constant held outputs alone would not detect instruction stalls.
    await load(h, 0, "DRIVE 0, 1\nSET 1, 1\nDELAY 4\nSET 1, 0\nDELAY 4\nJMP 1")
    await h.request(3)
    finished = [False]
    transitions = []
    async def monitor_isolation():
        previous = int(dut.uio_out.value) & 1
        last = None
        cycle = 0
        while not finished[0]:
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            assert int(dut.uio_oe.value) == 1
            value = int(dut.uio_out.value) & 1
            if value != previous:
                if last is not None:
                    assert cycle - last == (5 if previous else 6)
                transitions.append(cycle)
                last, previous = cycle, value
            cycle += 1
    observer = cocotb.start_soon(monitor_isolation())
    await load(h, 1, "\n".join(f"LI {i % 2}, {65535 - i * 997}" for i in range(63)) + "\nHALT")
    finished[0] = True
    await observer
    assert len(transitions) > 100
    await h.request(4)


@cocotb.test()
async def v2_capture_host_and_noninterference(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    waves = []
    for enabled in (False, True):
        await reset(dut)
        h = Host(dut)
        assert await h.request(11) == 0x20001F
        await h.request(0x21, payload=1)
        await load(h, 0, "DRIVE 0, 1\nMARK 1\nDELAY 8\nSET 1, 1\nDELAY 8\nSET 1, 0\nDELAY 8\nHALT")
        if enabled:
            await h.request(0x30, payload=0x0102)  # engine-0 marker
            await h.request(0x35, reject=True)  # active readout is rejected nonfatally
        wave = []
        async def observe():
            started = False
            for _ in range(3000):
                await RisingEdge(dut.clk)
                await Timer(1, unit="ns")
                running = int(dut.uo_out.value) >> 1 & 3
                if running:
                    started = True
                if started:
                    wave.append((int(dut.uio_out.value), int(dut.uio_oe.value), running))
                    if not running:
                        return
            raise AssertionError("engine did not run and halt")
        observer = cocotb.start_soon(observe())
        await h.request(3)
        await observer
        waves.append(wave)
        if enabled:
            await h.request(0x31)
            status, words = await capture_words(h)
            assert status & 63 == 3 << 3 | 2
            assert words[0] & 255 == 0x19  # running, marker, trigger
            assert words[-1] & 0xFFFFFF == 0  # released and halted
            assert words[0] >> 24 & 255 == 255
            records = [(word >> 32, word >> 16 & 255, word >> 8 & 255) for word in words]
            output_changes = [(cycle, out, oe) for i, (cycle, out, oe) in enumerate(records)
                              if i == 0 or (out, oe) != records[i - 1][1:]]
            assert [row[1:] for row in output_changes] == [(0, 1), (1, 1), (0, 1), (0, 0)]
            assert output_changes[2][0] - output_changes[1][0] == 9
    assert waves[0] == waves[1]

    # A full trace freezes while execution continues, and a shared host error
    # can itself trigger a later capture without losing the fault observation.
    await reset(dut)
    h = Host(dut)
    await h.request(0x21, payload=1)
    await load(h, 0, "DRIVE 0, 1\nSET 1, 1\nSET 1, 0\nJMP 1")
    await h.request(0x30)
    await h.request(3)
    status, words = await capture_words(h)
    assert status >> 6 == 32 and status & 7 == 6
    assert await h.request(6) == 1
    await h.request(4)
    assert (await capture_words(h))[1] == words
    await h.request(0x30, payload=3)
    await h.exchange(bits=39)
    await h.request(0x31)
    status, words = await capture_words(h)
    assert status & 7 == 2 and words[0] & 12 == 12
    await h.request(0x30)
    dut.ena.value = 0
    await ClockCycles(dut.clk, 5)
    dut.ena.value = 1
    await ClockCycles(dut.clk, 5)
    status, words = await capture_words(h)
    assert status >> 3 & 7 == 7  # disabled captures remain diagnostic-only
    assert status & 7 == 2 and words


@cocotb.test()
async def v2_uart_fault_capture_and_scenario(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    for bad_stop in (True, False):
        scenario = uart_demo(bad_stop=bad_stop, period=25)
        await reset(dut)
        h = Host(dut)
        await h.request(0x21, payload=1)
        for engine, image in enumerate(scenario["firmware"]):
            await batch(h, [command_frame(1, engine, i, word) for i, word in enumerate(image["words"])])
        await h.request(7, payload=scenario["tx"][0][0])
        await h.request(0x30, payload=0x0102)
        external = [255]
        levels = []
        def resolve_inputs():
            out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
            dut.uio_in.value = external[0] & ~oe | out & oe

        async def stimulus(origin):
            for edge in scenario["external_stimulus"]:
                wait = origin + edge["time_ns"] - int(get_sim_time(unit="ns"))
                assert wait > 0
                await Timer(wait, unit="ns")
                external[0] = edge["inputs"]
                resolve_inputs()

        async def observe():
            for _ in range(3000):
                await RisingEdge(dut.clk)
                await Timer(1, unit="ns")
                if int(dut.uo_out.value) & 6:
                    break
            else:
                raise AssertionError("scenario did not start")
            origin = int(get_sim_time(unit="ns")) + 39
            peer = cocotb.start_soon(stimulus(origin))
            index = 0
            events = scenario["expected"]["outputs"]
            for cycle in range(scenario["end_cycle"] + 1):
                await RisingEdge(dut.clk)
                await Timer(1, unit="ns")
                if index + 1 < len(events) and events[index + 1]["cycle"] == cycle:
                    index += 1
                out, oe = int(dut.uio_out.value), int(dut.uio_oe.value)
                assert (out, oe) == (events[index]["outputs"], events[index]["enables"]), cycle
                assert not int(dut.uo_out.value) & 8
                resolve_inputs()
                level = external[0] & ~oe | out & oe
                if not levels or levels[-1][1] != level:
                    levels.append((cycle * 40, level))
            await peer

        observer = cocotb.start_soon(observe())
        await h.request(0x20, payload=3)
        await observer
        await h.request(0x31)
        status, words = await capture_words(h)
        assert status & 63 == 3 << 3 | 2
        origin = words[0] >> 32
        records = [{"cycle": (word >> 32) - origin, "inputs": word >> 24 & 255,
                    "outputs": word >> 16 & 255, "enables": word >> 8 & 255,
                    "flags": word & 255} for word in words]
        expected = scenario["expected"]["capture"]
        assert records == [dict(record, cycle=record["cycle"] - expected["trigger_cycle"])
                           for record in expected["records"]]
        received = await h.request(0x13, addr=128)
        assert received >> 16 == 1 and received & 65535 == scenario["seed"] % 256
        assert decode_uart_events(levels, end_ns=(scenario["end_cycle"] + 1) * 40,
                                  period_ns=1000) == scenario["expected"]["decoded"]
        output = Path(__file__).parent / "output" / ("v2-uart-fault" if bad_stop else "v2-uart-fixed")
        write_artifacts(scenario, output)


@cocotb.test()
async def v2_bounded_firmware_playback(dut):
    cocotb.start_soon(Clock(dut.clk, 40, unit="ns").start())
    await reset(dut)
    h = Host(dut)
    await h.request(0x21, payload=0x0203)
    events = [OutputEvent(0, 1, 3), OutputEvent(1, 1, 1), OutputEvent(2, 0, 3),
              OutputEvent(65539, 1, 1), OutputEvent(65542, 0, 0)]
    end = 65545
    words = compile_schedule(events, end_cycle=end, owner=3, open_drain=2)
    await batch(h, [command_frame(1, address=i, value=word) for i, word in enumerate(words)])
    assert await batch(h, [command_frame(2, address=i) for i in range(len(words))]) == words

    async def observe():
        # START sets running on its control edge; execution begins next edge.
        for _ in range(3000):
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            if int(dut.uo_out.value) & 2:
                break
        else:
            raise AssertionError("playback did not start")
        index = 0
        for cycle in range(end + 2):
            await RisingEdge(dut.clk)
            await Timer(1, unit="ns")
            if index + 1 < len(events) and cycle == events[index + 1].cycle:
                index += 1
            expected = (events[index].outputs, events[index].enables) if cycle <= end else (0, 0)
            assert (int(dut.uio_out.value), int(dut.uio_oe.value)) == expected, cycle
            assert bool(int(dut.uo_out.value) & 2) == (cycle <= end), cycle
            assert not int(dut.uo_out.value) & 8
    observer = cocotb.start_soon(observe())
    await h.request(3)
    await observer


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
    for frequency, writes, reads in ((f, w, r) for f in (100_000, 400_000)
                                    for w, r in ((2, 0), (0, 3), (2, 3))):
        await reset(dut)
        h = Host(dut)
        await h.request(0x21, payload=0x0303)
        await load(h, 0, i2c_controller(write_count=writes, read_count=reads, frequency=frequency))
        await batch(h, tx_commands(0, [0x55, 0xA5][:writes]))
        peer = I2CTarget(read_data=bytes([0x81, 0x55, 0xA5]))
        edges = []
        async def resolve_bus():
            previous = 3
            while True:
                await Timer(10, unit="ns")
                value, enable = int(dut.uio_out.value), int(dut.uio_oe.value)
                bus = (value | ~enable) & ~peer.drive_low & 3
                if (bus ^ previous) & 2:
                    edges.append((get_sim_time(unit="ns"), bool(bus & 2)))
                previous = bus
                peer.observe(bus)
                dut.uio_in.value = 252 | ((value | ~enable) & ~peer.drive_low & 3)
        resolver = cocotb.start_soon(resolve_bus())
        await h.request(3)
        await Timer(750000 if frequency == 100_000 else 250000, unit="ns")
        assert await h.request(6) == 0
        assert peer.received == [0x55, 0xA5][:writes]
        assert peer.starts == (2 if writes and reads else 1)
        assert peer.stops == 1
        if reads:
            records = await batch(h, [command_frame(0x13)] * reads)
            assert [value & 65535 for value in records] == [0x81, 0x55, 0xA5][:reads]
            assert peer.acks == [0] * (reads - 1) + [1]
        resolver.cancel()
        high = [b[0] - a[0] for a, b in zip(edges, edges[1:]) if a[1]]
        low = [b[0] - a[0] for a, b in zip(edges, edges[1:]) if not a[1]]
        rising = [time for time, level in edges if level]
        fastest = min(b - a for a, b in zip(rising, rising[1:]))
        assert min(low) >= (4700 if frequency == 100_000 else 1300)
        assert min(high) >= (4000 if frequency == 100_000 else 600)
        assert 1e9 / frequency <= fastest <= 1.04e9 / frequency


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
