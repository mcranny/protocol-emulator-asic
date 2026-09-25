import random

import pytest

from protocol_emulator.v2.isa import decode
from protocol_emulator.v2.model import Engine
from protocol_emulator.v2.replay import OutputEvent, compile_schedule


def test_random_schedules_match_every_cycle_and_release_after_window():
    rng = random.Random(0xB0A7)
    for _ in range(50):
        end = rng.randrange(40, 200)
        cycles = [0, *sorted(rng.sample(range(1, end + 1), rng.randrange(1, 15)))]
        events = []
        for cycle in cycles:
            enables = rng.randrange(256)
            events.append(OutputEvent(cycle, rng.randrange(256) & enables & 0xF0, enables))
        engine = Engine(capacity=64)
        engine.owner, engine.open_drain = 255, 15
        engine.load(compile_schedule(events, end_cycle=end, owner=255, open_drain=15))
        engine.start()
        pending, state = iter(events), None
        next_event = next(pending)
        for cycle in range(end + 1):
            if next_event is not None and next_event.cycle == cycle:
                state = next_event
                next_event = next(pending, None)
            engine.tick()
            outputs, enables = engine.pins
            assert (outputs & enables, enables) == (state.outputs, state.enables), cycle
            assert engine.running and not engine.errors
        engine.tick()
        assert not engine.running and engine.pins[1] == 0 and not engine.errors


def test_long_delays_split_without_extra_cycles():
    events = [OutputEvent(0, 0, 1), OutputEvent(65537, 1, 1)]
    words = compile_schedule(events, end_cycle=131073, owner=1)
    assert [decode(word).name for word in words] == ["DRIVE", "DELAY", "DELAY", "DRIVE", "DELAY", "DELAY", "HALT"]
    engine = Engine(capacity=64)
    engine.owner = 1
    engine.load(words)
    engine.start()
    for cycle in range(131075):
        engine.tick()
        assert engine.pins[1] == int(cycle < 131074)
        if engine.running:
            assert engine.pins[0] == int(cycle >= 65537)


@pytest.mark.parametrize("events,end,owner,od", [
    ([], 10, 1, 0),
    ([OutputEvent(1, 0, 1)], 10, 1, 0),
    ([OutputEvent(0, 0, 1), OutputEvent(0, 1, 1)], 10, 1, 0),
    ([OutputEvent(0, 0, 1), OutputEvent(0.5, 1, 1)], 10, 1, 0),
    ([OutputEvent(0, 0, 1), OutputEvent(11, 1, 1)], 10, 1, 0),
    ([OutputEvent(0, 2, 2)], 10, 1, 0),
    ([OutputEvent(0, 1, 0)], 10, 1, 0),
    ([OutputEvent(0, 1, 1)], 10, 1, 1),
    ([OutputEvent(0, 0, 1)], 10, 1, 2),
    ([OutputEvent(False, 0, 1)], 10, 1, 0),
])
def test_unachievable_or_unsafe_schedule_is_rejected(events, end, owner, od):
    with pytest.raises(ValueError):
        compile_schedule(events, end_cycle=end, owner=owner, open_drain=od)


def test_capacity_includes_hold_and_halt_and_rejects_huge_gap():
    events = [OutputEvent(i, i % 2, 1) for i in range(63)]
    assert len(compile_schedule(events, end_cycle=62, owner=1)) == 64
    with pytest.raises(ValueError, match="65 instructions"):
        compile_schedule(events, end_cycle=63, owner=1)
    with pytest.raises(ValueError, match="capacity"):
        compile_schedule([OutputEvent(0, 0, 1)], end_cycle=0xFFFFFFFF, owner=1)
