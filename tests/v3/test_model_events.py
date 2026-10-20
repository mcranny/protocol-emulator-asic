"""Event instructions: watchers, events, aborts, MARK and CLEARMISS."""
from collections.abc import Callable

from protocol_emulator.v3 import isa, model


def new_engine(source: str, owner: int = 0xFF) -> model.Engine:
    """Assemble ``source`` onto a fresh engine owning ``owner``'s pins."""
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=owner)
    engine.load(isa.assemble(source))
    return engine


def observe_byte(engine: model.Engine, value: int, now: int) -> None:
    """Feed ``value`` as the engine-visible level of all eight pins at ``now``."""
    for index in range(8):
        engine.pins[index].observe((value >> index) & 1, now)


def run_until_halt(
    engine: model.Engine,
    byte_at: Callable[[int], int],
    max_cycles: int = 100,
    start_now: int = 0,
) -> None:
    """Start, then step with ``byte_at(now)`` observed each cycle until halt."""
    base = model.Timebase()
    base.now = start_now
    engine.start(base.now)
    for _ in range(max_cycles):
        observe_byte(engine, byte_at(base.now), base.now)
        engine.step(base.now)
        for cell in engine.pins:
            cell.tick(base.now)
        base.tick()
        if engine.halted:
            return


# -- watchers and the event bits ----------------------------------------


def test_a_watcher_sets_its_event_on_a_masked_pattern_with_an_edge() -> None:
    engine = new_engine("NOP\nHALT\n")
    engine.watches = [(0x01, 0x01, 0x01)]
    engine.start(0)
    observe_byte(engine, 0x00, 0)
    engine.step(0)
    assert engine.events == 0, "no edge yet, no event"
    observe_byte(engine, 0x01, 1)
    engine.step(1)
    assert engine.events & 1, "the masked pattern arrived with a watched edge"


def test_a_watcher_needs_both_the_pattern_and_the_edge() -> None:
    engine = new_engine("NOP\nNOP\nHALT\n")
    engine.watches = [(0x01, 0x01, 0x02)]  # pattern on pin 0, but edge watched on pin 1
    engine.start(0)
    observe_byte(engine, 0x00, 0)
    engine.step(0)
    observe_byte(engine, 0x01, 1)  # pin 0 matches, but the edge is on pin 0, not pin 1
    engine.step(1)
    assert engine.events == 0


def test_brc_event0_branches_when_the_watcher_fires() -> None:
    source = "loop:\nBRC target=hit condition=event0\nJMP target=loop\nhit:\nHALT\n"
    engine = new_engine(source)
    engine.watches = [(0x01, 0x01, 0x01)]
    run_until_halt(engine, lambda now: 0x01 if now >= 4 else 0x00)
    assert engine.halted and engine.errors == 0
    assert engine.events & 1


def test_brc_event1_branches_for_the_second_watcher() -> None:
    source = "loop:\nBRC target=hit condition=event1\nJMP target=loop\nhit:\nHALT\n"
    engine = new_engine(source)
    engine.watches = [(0x01, 0x01, 0x01), (0x02, 0x02, 0x02)]
    run_until_halt(engine, lambda now: 0x02 if now >= 4 else 0x00)
    assert engine.halted and engine.errors == 0
    assert engine.events & 2
    assert not engine.events & 1, "the pin-0 watcher never saw its pattern"


# -- WAITEVENT ----------------------------------------------------------


def test_waitevent_retires_when_a_masked_event_is_set() -> None:
    engine = new_engine("ADVANCE ticks=50\nWAITEVENT mask=0x01\nHALT\n")
    engine.watches = [(0x01, 0x01, 0x01)]
    run_until_halt(engine, lambda now: 0x01 if now >= 10 else 0x00)
    assert engine.halted and engine._flag_timeout is False


def test_waitevent_times_out_on_the_cursor() -> None:
    engine = new_engine("ADVANCE ticks=5\nWAITEVENT mask=0x01\nHALT\n")
    run_until_halt(engine, lambda now: 0x00)
    assert engine._flag_timeout is True


def test_waitevent_is_never_aborted() -> None:
    engine = new_engine("ADVANCE ticks=8\nWAITEVENT mask=0x01\nHALT\n")
    engine.start(0)
    engine.abort_mask = 0x02
    engine.events = 0x02  # an abort-enabled event outside the wait mask
    for now in range(20):
        observe_byte(engine, 0x00, now)
        engine.step(now)
        if engine.halted:
            break
    assert engine._flag_timeout is True, "WAITEVENT ignores aborts and times out"
    assert engine._flag_interrupted is False


# -- aborts (interrupt) and same-cycle priority -------------------------


def test_waitfor_ends_interrupted_on_an_abort_event() -> None:
    source = (
        "ADVANCE ticks=50\n"
        "WAITFOR pin=0 level=1\n"
        "BRC target=ok condition=interrupted\n"
        "FAIL code=1\n"
        "ok:\n"
        "HALT\n"
    )
    engine = new_engine(source)
    engine.watches = [(0x02, 0x02, 0x02)]  # watcher 0 fires on a pin-1 edge
    engine.abort_mask = 0x01  # watcher 0 aborts
    # Pin 0 stays low so WAITFOR never matches; pin 1 rises at now=10 to abort.
    run_until_halt(engine, lambda now: 0x02 if now >= 10 else 0x00)
    assert engine.halted and engine.errors == 0
    assert engine._flag_interrupted is True
    assert engine._flag_timeout is False


def test_abort_outranks_a_match_in_the_same_cycle() -> None:
    # V2 priority: self.timeout = not matched and not aborted; self.interrupted =
    # aborted. So an abort reports interrupted even when the level also matches.
    engine = new_engine("ADVANCE ticks=50\nWAITFOR pin=0 level=1\nHALT\n")
    engine.start(0)
    engine.abort_mask = 0x01
    engine.events = 0x01  # abort event already set (level-sensitive)
    observe_byte(engine, 0x01, 0)
    engine.step(0)  # ADVANCE
    observe_byte(engine, 0x01, 1)  # pin 0 high: the level matches too
    engine.step(1)  # WAITFOR: match and abort on the same cycle
    assert engine._flag_interrupted is True
    assert engine._flag_timeout is False


# -- MARK, CLEAREVENT, CLEARMISS ----------------------------------------


def test_mark_sets_the_marker_for_exactly_one_cycle() -> None:
    engine = new_engine("MARK code=0x5A\nNOP\nHALT\n")
    engine.start(0)
    engine.step(0)
    assert engine.marker == 0x5A
    engine.step(1)
    assert engine.marker == 0, "the marker is a one-cycle pulse"


def test_clearevent_clears_exactly_the_masked_bits() -> None:
    engine = new_engine("CLEAREVENT mask=0x01\nHALT\n")
    engine.start(0)
    engine.events = 0x03
    engine.step(0)
    assert engine.events == 0x02


def test_clearmiss_clears_only_owned_masked_pins() -> None:
    engine = new_engine("CLEARMISS mask=0xFF\nHALT\n", owner=0x0F)
    engine.start(0)
    for cell in engine.pins:
        cell.missed = True
    engine.step(0)
    assert all(engine.pins[i].missed is False for i in range(4)), "owned pins cleared"
    assert all(engine.pins[i].missed is True for i in range(4, 8)), "unowned pins untouched"
    assert engine.errors == 0, "an unowned mask bit does not fault"
