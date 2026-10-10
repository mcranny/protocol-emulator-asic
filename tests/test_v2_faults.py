"""Protocol aborts and externally imposed faults, independent of instruction PCs."""
import pytest

from protocol_emulator.v2.firmware import spi_target, spi_controller, i2c_controller, i2c_bus_clear
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.model import Engine, Watch, FIRMWARE
from protocol_emulator.model import TIMEOUT


def loaded(source, owner, open_drain=0):
    e = Engine(capacity=64)
    e.owner, e.open_drain = owner, open_drain
    e.load(assemble(source, capacity=64))
    return e


@pytest.mark.parametrize('mode', range(4))
@pytest.mark.parametrize('partial_bits', range(8))
def test_spi_target_abort_discards_partial_byte_and_recovers(mode, partial_bits):
    e = loaded(spi_target(mode), 8)
    e.watches = (Watch(4, 4, 4),)
    e.abort_mask = 1
    e.push([0xA5, 0x5A])
    e.start()
    idle = (mode >> 1) << 1
    def hold(pins, cycles):
        for _ in range(cycles): e.tick(pins)
    hold(4 | idle, 20)
    hold(idle, 30)
    for _ in range(partial_bits):
        hold(idle ^ 2, 13)
        hold(idle, 12)
    # CS may release while an edge wait is active; no partial RX is committed.
    hold(4 | idle, 30)
    assert e.running and not e.errors and not e.rx and e.pins[1] == 0
    hold(idle | 1, 30)
    bits = []
    for _ in range(8):
        if not mode & 1: bits.append(e.pins[0] >> 3 & 1)
        hold((idle ^ 2) | 1, 13)
        if mode & 1: bits.append(e.pins[0] >> 3 & 1)
        hold(idle | 1, 12)
    hold(4 | idle, 30)
    assert bits == [0x5A >> bit & 1 for bit in range(7, -1, -1)]
    assert list(e.rx) == [(255, 0)] and not e.errors and e.pins[1] == 0


@pytest.mark.parametrize('mode', range(4))
def test_spi_initial_underrun_releases_target(mode):
    e = loaded(spi_target(mode), 8)
    e.start()
    for _ in range(40): e.tick((mode >> 1) << 1)
    # CPHA=1 defers underrun until the controller requests the first bit.
    for _ in range(20): e.tick(((mode >> 1) ^ 1) << 1)
    assert not e.running and e.errors & FIRMWARE and e.firmware_error == 1
    assert e.pins[1] == 0 and not e.rx


@pytest.mark.parametrize('mode', range(4))
def test_spi_controller_starvation_keeps_cs_inactive(mode):
    e = loaded(spi_controller(mode), 7)
    e.start()
    for _ in range(300): e.tick(8)
    assert e.running and not e.errors and e.pins == (((mode >> 1) << 1) | 4, 7)
    e.step(command=(4, 0, 0))
    assert e.pins[1] == 0


@pytest.mark.parametrize('frequency', [100_000, 400_000])
@pytest.mark.parametrize('fault', ['nack', 'arbitration', 'sda_stuck', 'scl_stuck'])
def test_i2c_controller_faults_release_bus(frequency, fault):
    e = loaded(i2c_controller(frequency=frequency), 3, 3)
    e.push([0x55])
    e.start()
    started = False
    for cycle in range(80000):
        out, oe = e.pins
        bus = (out | ~oe) & 3
        started |= oe != 0
        if fault == 'sda_stuck': bus &= 2
        elif fault == 'scl_stuck': bus &= 1
        elif fault == 'arbitration' and started: bus &= 2
        e.tick(bus)
        if not e.running: break
    assert not e.running and e.pins[1] == 0 and not e.rx
    if fault in ('sda_stuck', 'scl_stuck'):
        assert e.errors & TIMEOUT
    else:
        assert e.errors & FIRMWARE
        assert e.firmware_error == (8 if fault == 'nack' else 4)


@pytest.mark.parametrize('frequency', [100_000, 400_000])
@pytest.mark.parametrize('release_after', [1, 5, 9, None])
def test_bus_clear_nine_pulses_then_stop(frequency, release_after):
    e = loaded(i2c_bus_clear(frequency), 3, 3)
    e.start()
    previous, pulses, stops = 2, 0, 0  # SCL idle high, target holding SDA low
    released = False
    for _ in range(80000):
        out, oe = e.pins
        bus = (out | ~oe) & 3
        if bus & 2 and not previous & 2: pulses += 1
        if release_after is not None and pulses >= release_after: released = True
        if not released: bus &= 2
        if bus & 3 == 3 and previous == 2: stops += 1
        e.tick(bus)
        previous = bus
        if not e.running: break
    assert not e.running and e.pins[1] == 0
    # Nine recovery pulses, then the SCL rise that prepares the final STOP.
    assert pulses == 10
    if release_after is None:
        assert e.errors & TIMEOUT
    else:
        assert not e.errors and stops >= 1
