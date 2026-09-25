"""Independent schedules for the candidate architecture, separate from V1."""
import pytest

from protocol_emulator.isa import encode as v1_encode
from protocol_emulator.v2.isa import assemble, decode, encode, EXTENSIONS
from protocol_emulator.v2.model import Device, Engine, Watch, OWNERSHIP
from protocol_emulator.model import OVERFLOW
from protocol_emulator.v2.firmware import uart_rx, uart_tx, spi_controller, spi_target


def engine(source, owner=0):
    e = Engine()
    e.owner = owner
    e.load(assemble(source))
    e.start()
    return e


def test_v1_encoding_compatibility():
    for name, operands in [("NOP", ()), ("SET", (255, 85)), ("OE", (255, 255)),
                           ("LI", (1, 65535)), ("WAIT", (7, 1, 65535)),
                           ("DJNZ", (1, 63)), ("JMP", (63,)), ("HALT", ())]:
        assert encode(name, *operands) == v1_encode(name, *operands)
    assert assemble("JMP last\n" + "NOP\n" * 126 + "last: HALT")[-1] == 0xC00000
    with pytest.raises(ValueError):
        assemble("JMP 64", capacity=64)
    with pytest.raises(ValueError):
        decode(0xDF8000)
    with pytest.raises(ValueError):
        decode(0xD20001)


def test_ownership_and_atomic_start():
    d = Device()
    d.configure((1, 2), (0, 2))
    with pytest.raises(ValueError):
        d.configure((1, 1))
    assert [e.owner for e in d.engines] == [1, 2]
    for e, value in zip(d.engines, (1, 2)):
        e.load(assemble(f"DRIVE {value}, {value}\nJMP 0"))
    d.start(3)
    assert d.tick() == (1, 1)  # engine 1's open-drain high is released
    with pytest.raises(ValueError):
        d.configure((2, 1))
    assert d.tick(frame_error=True)[1] == 0
    e = engine("DRIVE 2, 2", owner=1)
    e.tick()
    assert e.errors == OWNERSHIP and e.pins[1] == 0


def test_rx_overflow_preserves_old_data_and_releases():
    e = engine("DRIVE 1, 1\nRX 0\nJMP 1", owner=1)
    for _ in range(40):
        e.tick()
    assert list(e.rx) == [(0, 0)] * 16
    assert e.errors == OVERFLOW and e.pins[1] == 0


def test_wait_boundary_abort_and_program_isolation():
    e = engine("LI 1, 2\nWAITFOR 0, 1\nHALT")
    e.tick()
    e.tick()
    e.tick(1)
    assert e.pc == 2 and not e.timeout
    e = engine("LI 1, 10\nWAITFOR 0, 1\nHALT")
    e.watches = (Watch(2, 2, 2),)
    e.abort_mask = 1
    e.tick()
    e.tick(2)
    assert e.interrupted and e.pc == 2
    before = list(e.program)
    with pytest.raises(ValueError):
        e.load([0])
    assert e.program == before


@pytest.mark.parametrize("period", [25, 217])
@pytest.mark.parametrize("mismatch", [-0.02, 0, 0.02])
@pytest.mark.parametrize("phase", [0, 0.25, 0.75])
def test_uart_receive_independent_schedule(period, mismatch, phase):
    e = engine(uart_rx(period))
    values = [0x00, 0x55, 0xA5, 0xFF, 0x81]
    peer_period = period / (1 + mismatch)
    start = 20 + phase
    # Two-flop synchronization plus the architecture's previous-stage view.
    pipeline = [2, 2, 2]
    for cycle in range(int(start + len(values) * 10 * peer_period + period * 2)):
        position = (cycle - start) / peer_period
        if 0 <= position < len(values) * 10:
            frame, bit = divmod(int(position), 10)
            level = 0 if bit == 0 else (1 if bit == 9 else values[frame] >> (bit - 1) & 1)
        else:
            level = 1
        pipeline.append(level << 1)
        e.tick(pipeline.pop(0))
    assert list(e.rx) == [(value, 0) for value in values]
    assert not e.errors


@pytest.mark.parametrize("period", [25, 217])
def test_uart_transmit_preserves_v1_timing(period):
    e = engine(uart_tx(period), 1)
    data = bytes(range(16))
    e.push(data)
    wave = []
    for _ in range(10 * period * len(data) + 10):
        e.tick()
        wave.append(e.pins[0] & 1)
    for frame, value in enumerate(data):
        for bit, expected in enumerate([0] + [value >> i & 1 for i in range(8)] + [1]):
            begin = 4 + (frame * 10 + bit) * period
            assert wave[begin:begin + period] == [expected] * period


@pytest.mark.parametrize("mode", range(4))
def test_spi_controller_independent_edge_decoder(mode):
    e = engine(spi_controller(mode), 7)
    e.push([0xA5])
    cpol, cpha = mode >> 1, mode & 1
    received = []
    previous = (cpol << 1) | 4
    # MISO held high: RX must independently assemble FF.
    for _ in range(400):
        e.tick(8)
        current, oe = e.pins
        if not current & 4 and (current ^ previous) & 2:
            leading = (current >> 1 & 1) != cpol
            if leading != bool(cpha):
                received.append(current & 1)
        previous = current
    assert received == [1, 0, 1, 0, 0, 1, 0, 1]
    assert list(e.rx) == [(255, 0)]
    assert not e.errors


@pytest.mark.parametrize("mode", range(4))
def test_spi_target_first_byte_against_independent_peer(mode):
    e = engine(spi_target(mode), 8)
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
            if t in [int((2 * bit + (1 if cpha else 0)) * 12.5 + 0.5) for bit in range(8)]:
                received.append(e.pins[0] >> 3 & 1)
        e.tick(pins)
    assert received == [1, 0, 1, 0, 0, 1, 0, 1]
    assert list(e.rx) == [(255, 0)]


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("phase", [0.0, 0.4, 0.9])
def test_spi_target_back_to_back_with_synchronizer(mode, phase):
    e = engine(spi_target(mode), 8)
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
    assert received == [byte >> bit & 1 for byte in expected for bit in range(7, -1, -1)]
    assert list(e.rx) == [(255, 0)] * 3


@pytest.mark.parametrize("period", [25, 217])
def test_uart_false_start_framing_break_and_recovery(period):
    e = engine(uart_rx(period))
    for _ in range(10):
        e.tick(2)
    # A short low pulse cannot create a received byte.
    for _ in range(3):
        e.tick(0)
    for _ in range(2 * period):
        e.tick(2)
    assert not e.rx
    # Nonzero byte with a low stop bit: one framing record, then await idle.
    for bit in [0] + [0xA5 >> i & 1 for i in range(8)] + [0]:
        for _ in range(period):
            e.tick(bit << 1)
    for _ in range(2 * period):
        e.tick(2)
    assert list(e.rx) == [(0xA5, 1)]
    for _ in range(12 * period):
        e.tick(0)
    for _ in range(2 * period):
        e.tick(2)
    assert list(e.rx) == [(0xA5, 1), (0, 3)]
    for bit in [0] + [0x55 >> i & 1 for i in range(8)] + [1]:
        for _ in range(period):
            e.tick(bit << 1)
    assert list(e.rx) == [(0xA5, 1), (0, 3), (0x55, 0)]
    assert not e.errors
