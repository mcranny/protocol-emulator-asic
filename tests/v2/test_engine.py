"""Independent V2 engine behavior checks."""

import pytest
from protocol_emulator.model import OVERFLOW
from protocol_emulator.v2.isa import assemble
from protocol_emulator.v2.model import OWNERSHIP, Device, Watch


def test_ownership_and_atomic_start(make_engine):
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
    e = make_engine("DRIVE 2, 2", owner=1)
    e.tick()
    assert e.errors == OWNERSHIP and e.pins[1] == 0


def test_rx_overflow_preserves_old_data_and_releases(make_engine):
    e = make_engine("DRIVE 1, 1\nRX 0\nJMP 1", owner=1)
    for _ in range(40):
        e.tick()
    assert list(e.rx) == [(0, 0)] * 16
    assert e.errors == OVERFLOW and e.pins[1] == 0


def test_wait_boundary_abort_and_program_isolation(make_engine):
    e = make_engine("LI 1, 2\nWAITFOR 0, 1\nHALT")
    e.tick()
    e.tick()
    e.tick(1)
    assert e.pc == 2 and not e.timeout
    e = make_engine("LI 1, 10\nWAITFOR 0, 1\nHALT")
    e.watches = (Watch(2, 2, 2),)
    e.abort_mask = 1
    e.tick()
    e.tick(2)
    assert e.interrupted and e.pc == 2
    before = list(e.program)
    with pytest.raises(ValueError):
        e.load([0])
    assert e.program == before
