"""Independent V2 spi behavior checks."""

import pytest
from protocol_emulator.v2.firmware import spi_controller, spi_target
from protocol_emulator.v2.model import Watch


@pytest.mark.parametrize("mode", range(4), ids=["mode=0", "mode=1", "mode=2", "mode=3"])
@pytest.mark.parametrize(
    "frequency", [100_000, 1_000_000], ids=["frequency=100000", "frequency=1000000"]
)
def test_spi_controller_independent_edge_decoder(make_engine, mode, frequency):
    e = make_engine(spi_controller(mode, frequency=frequency), 7)
    e.push([0xA5])
    cpol, cpha = mode >> 1, mode & 1
    received = []
    edges = []
    previous = (cpol << 1) | 4
    # MISO held high: RX must independently assemble FF.
    for cycle in range(25_000_000 // frequency * 12):
        e.tick(8)
        current, oe = e.pins
        if not current & 4 and (current ^ previous) & 2:
            edges.append(cycle)
            leading = (current >> 1 & 1) != cpol
            if leading != bool(cpha):
                received.append(current & 1)
        previous = current
    assert received == [1, 0, 1, 0, 0, 1, 0, 1]
    assert list(e.rx) == [(255, 0)]
    assert not e.errors
    assert len(edges) == 16
    assert {b - a for a, b in zip(edges, edges[2:])} == {25_000_000 // frequency}
    assert min(b - a for a, b in zip(edges, edges[1:])) == 25_000_000 // frequency // 2


@pytest.mark.parametrize("mode", range(4), ids=["mode=0", "mode=1", "mode=2", "mode=3"])
def test_spi_target_first_byte_against_independent_peer(make_engine, mode):
    e = make_engine(spi_target(mode), 8)
    e.watches = (Watch(4, 4, 4),)
    e.abort_mask = 1
    e.push([0xA5])
    cpol, cpha = mode >> 1, mode & 1
    received = []
    # Generous CS setup but exact 1 MHz SCK; no model-derived edge schedule.
    for cycle in range(300):
        if cycle < 10 or cycle >= 230:
            pins = 4 | cpol << 1 | 1
        elif cycle < 30:
            pins = cpol << 1 | 1
        else:
            t = cycle - 30
            edge = t * 2 // 25
            pins = ((cpol ^ (1 if edge % 2 == 0 else 0)) << 1) | 1
            if t in [
                int((2 * bit + (1 if cpha else 0)) * 12.5 + 0.5) for bit in range(8)
            ]:
                received.append(e.pins[0] >> 3 & 1)
        e.tick(pins)
    assert received == [1, 0, 1, 0, 0, 1, 0, 1]
    assert list(e.rx) == [(255, 0)]


@pytest.mark.parametrize("mode", range(4), ids=["mode=0", "mode=1", "mode=2", "mode=3"])
@pytest.mark.parametrize(
    "phase", [0.0, 0.4, 0.9], ids=["phase=0.0", "phase=0.4", "phase=0.9"]
)
def test_spi_target_back_to_back_with_synchronizer(make_engine, mode, phase):
    e = make_engine(spi_target(mode), 8)
    e.watches = (Watch(4, 4, 4),)
    e.abort_mask = 1
    expected = [0xA5, 0x81, 0x5A]
    e.push(expected)
    cpol, cpha = mode >> 1, mode & 1
    received = []
    pipeline = [4 | cpol << 1 | 1] * 3
    # External sampling precedes engine execution at a system edge. Fractional
    # edge phases are quantized only when entering the input synchronizer.
    previous_external = 4 | cpol << 1 | 1
    for cycle in range(850):
        if cycle < 10 or cycle >= 830:
            pins = 4 | cpol << 1 | 1
        elif cycle < 50 + phase or cycle >= 650 + phase:
            pins = cpol << 1 | 1
        else:
            edge = int((cycle - 50 - phase) / 12.5)
            pins = ((cpol ^ (1 if edge % 2 == 0 else 0)) << 1) | 1
        if not pins & 4 and (pins ^ previous_external) & 2:
            leading = (pins >> 1 & 1) != cpol
            if leading != bool(cpha):
                received.append(e.pins[0] >> 3 & 1)
        pipeline.append(pins)
        e.tick(pipeline.pop(0))
        previous_external = pins
    assert received == [
        byte >> bit & 1 for byte in expected for bit in range(7, -1, -1)
    ]
    assert list(e.rx) == [(255, 0)] * 3
