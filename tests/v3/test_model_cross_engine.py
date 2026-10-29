"""Two engines over one set of pins: a shared deadline, a stall, and an event."""
from protocol_emulator.v3 import isa, model


def run_pair(source0: str, source1: str, cycles: int, watches0=()):
    """Start both engines at time 0 and step them in engine-0-first order."""
    pins = [model.PinCell() for _ in range(8)]
    engine0 = model.Engine(pins=pins, owner=0x0F)
    engine1 = model.Engine(pins=pins, owner=0xF0)
    engine0.load(isa.assemble(source0))
    engine1.load(isa.assemble(source1))
    engine0.watches = list(watches0)
    base = model.Timebase()
    engine0.start(base.now)
    engine1.start(base.now)
    fired = {}
    events = []
    for _ in range(cycles):
        now = base.now
        for cell in pins:
            cell.observe(cell.level, now)
        engine0.step(now)
        engine1.step(now, crc_grant=not engine0.crc_used)
        for index, cell in enumerate(pins):
            before = cell.drive()
            cell.tick(now)
            if cell.drive() != before and index not in fired:
                fired[index] = now
        events.append((now, engine0.events, engine1.halted, engine1.errors))
        base.tick()
    return engine0, engine1, pins, fired, events


def test_both_engines_commit_on_the_shared_deadline() -> None:
    source = "ADVANCE ticks=20\nOUT pin={pin} level=1 offset=0\nHALT\n"
    engine0, engine1, pins, fired, _events = run_pair(
        source.format(pin=0), source.format(pin=4), cycles=30
    )
    assert engine0.halted and engine1.halted
    assert engine0.errors == engine1.errors == 0
    assert fired[0] == fired[4] == 20
    assert pins[0].drive() == pins[4].drive() == (1, 1)
    assert all(pins[index].drive() == (0, 0) for index in (1, 2, 3, 5, 6, 7))


def test_a_stalled_engine_does_not_stop_the_other() -> None:
    waiting = "ADVANCE ticks=500\nWAITTIME\nHALT\n"
    running = "LI reg=0 value=7\nHALT\n"
    engine0, engine1, pins, fired, _events = run_pair(waiting, running, cycles=8)
    assert engine0.running and engine0.stalled and not engine0.halted
    assert engine1.halted and engine1.errors == 0
    assert engine1.regs[0] == 7
    assert fired == {}
    assert all(cell.drive() == (0, 0) for cell in pins)


def test_an_event_is_absent_then_sticky_and_leaves_the_other_engine_alone() -> None:
    waiting = "ADVANCE ticks=40\nWAITEVENT mask=1\nHALT\n"
    finished = "LI reg=0 value=1\nHALT\n"
    pins = [model.PinCell() for _ in range(8)]
    engine0 = model.Engine(pins=pins, owner=0x0F)
    engine1 = model.Engine(pins=pins, owner=0xF0)
    engine0.load(isa.assemble(waiting))
    engine1.load(isa.assemble(finished))
    engine0.watches = [(0x01, 0x01, 0x01)]
    base = model.Timebase()
    engine0.start(base.now)
    engine1.start(base.now)
    edge_at = 8
    saw_edge = False
    for _ in range(12):
        now = base.now
        level = 1 if now >= edge_at else 0
        for cell in pins:
            cell.observe(level, now)
        engine0.step(now)
        engine1.step(now, crc_grant=not engine0.crc_used)
        for cell in pins:
            cell.tick(now)
        if now < edge_at:
            assert engine0.events == 0, "the watcher must be quiet before the edge"
            assert not engine0.halted
        if now == edge_at:
            assert engine0.events == 1, "the edge sets the watcher this cycle"
            saw_edge = True
        if now > edge_at:
            assert engine0.events == 1, "the event stays set until CLEAREVENT"
        if now >= 3:
            assert engine1.halted and engine1.errors == 0 and engine1.regs[0] == 1
        base.tick()
    assert saw_edge
    assert engine0.halted and engine0.errors == 0
