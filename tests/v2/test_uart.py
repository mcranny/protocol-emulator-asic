"""Independent V2 uart behavior checks."""

import pytest
from protocol_emulator.v2.firmware import uart_rx, uart_tx


@pytest.mark.parametrize("period", [25, 217], ids=["period=25", "period=217"])
@pytest.mark.parametrize(
    "mismatch", [-0.02, 0, 0.02], ids=["mismatch=-0.02", "mismatch=0", "mismatch=0.02"]
)
@pytest.mark.parametrize(
    "phase", [0, 0.25, 0.75], ids=["phase=0", "phase=0.25", "phase=0.75"]
)
def test_uart_receive_independent_schedule(make_engine, period, mismatch, phase):
    e = make_engine(uart_rx(period))
    values = [0x00, 0x55, 0xA5, 0xFF, 0x81]
    peer_period = period / (1 + mismatch)
    start = 20 + phase
    # Two-flop synchronization plus the architecture's previous-stage view.
    pipeline = [2, 2, 2]
    for cycle in range(int(start + len(values) * 10 * peer_period + period * 2)):
        position = (cycle - start) / peer_period
        if 0 <= position < len(values) * 10:
            frame, bit = divmod(int(position), 10)
            level = (
                0 if bit == 0 else (1 if bit == 9 else values[frame] >> (bit - 1) & 1)
            )
        else:
            level = 1
        pipeline.append(level << 1)
        e.tick(pipeline.pop(0))
    assert list(e.rx) == [(value, 0) for value in values]
    assert not e.errors


@pytest.mark.parametrize("period", [25, 217], ids=["period=25", "period=217"])
def test_uart_transmit_preserves_v1_timing(make_engine, period):
    e = make_engine(uart_tx(period), 1)
    data = bytes(range(16))
    e.push(data)
    wave = []
    for _ in range(10 * period * len(data) + 10):
        e.tick()
        wave.append(e.pins[0] & 1)
    for frame, value in enumerate(data):
        for bit, expected in enumerate([0] + [value >> i & 1 for i in range(8)] + [1]):
            begin = 4 + (frame * 10 + bit) * period
            assert wave[begin : begin + period] == [expected] * period


@pytest.mark.parametrize("period", [25, 217], ids=["period=25", "period=217"])
def test_uart_false_start_framing_break_and_recovery(make_engine, period):
    e = make_engine(uart_rx(period))
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
