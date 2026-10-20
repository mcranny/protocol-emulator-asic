"""Pin cell semantics: deadlines, misses, open drain, edge timestamps."""
import pytest

from protocol_emulator.v3 import model


def test_signed_distance_is_positive_just_before_the_deadline() -> None:
    assert model.signed_distance(due=100, now=90) == 10


def test_signed_distance_is_negative_just_after_the_deadline() -> None:
    assert model.signed_distance(due=90, now=100) == -10


def test_signed_distance_handles_wraparound() -> None:
    # due is 10 ticks after now, across the 24-bit wrap.
    assert model.signed_distance(due=5, now=0xFFFFFB) == 10


def test_signed_distance_saturates_at_the_window_edges() -> None:
    assert model.signed_distance(due=0x7FFFFF, now=0) == 0x7FFFFF
    assert model.signed_distance(due=0x800000, now=0) == -0x800000


def test_a_scheduled_change_does_not_apply_early() -> None:
    cell = model.PinCell()
    cell.schedule(due=10, value=1, oe=1, now=0)
    for now in range(0, 10):
        cell.tick(now)
        assert cell.value == 0
    cell.tick(10)
    assert cell.value == 1


def test_the_slot_frees_when_it_fires() -> None:
    cell = model.PinCell()
    cell.schedule(due=3, value=1, oe=1, now=0)
    assert cell.pending is not None
    cell.tick(3)
    assert cell.pending is None


def test_a_past_deadline_applies_immediately_and_sets_missed() -> None:
    cell = model.PinCell()
    cell.schedule(due=5, value=1, oe=1, now=9)
    assert cell.value == 1
    assert cell.missed is True
    assert cell.pending is None


def test_an_on_time_commit_does_not_set_missed() -> None:
    cell = model.PinCell()
    cell.schedule(due=5, value=1, oe=1, now=5)
    assert cell.value == 1
    assert cell.missed is False


def test_writing_an_occupied_slot_is_an_error() -> None:
    cell = model.PinCell()
    cell.schedule(due=10, value=1, oe=1, now=0)
    with pytest.raises(model.SlotBusy):
        cell.schedule(due=11, value=0, oe=1, now=0)


def test_missed_is_sticky_until_cleared() -> None:
    cell = model.PinCell()
    cell.schedule(due=0, value=1, oe=1, now=5)
    cell.tick(6)
    cell.tick(7)
    assert cell.missed is True
    cell.clear_missed()
    assert cell.missed is False


def test_open_drain_never_drives_high() -> None:
    cell = model.PinCell(open_drain=True)
    cell.schedule(due=0, value=1, oe=1, now=0)
    assert cell.drive() == (0, 0), "a requested high must become high impedance"


def test_open_drain_still_drives_low() -> None:
    cell = model.PinCell(open_drain=True)
    cell.schedule(due=0, value=0, oe=1, now=0)
    assert cell.drive() == (0, 1)


def test_an_input_edge_is_timestamped_with_its_polarity() -> None:
    cell = model.PinCell()
    cell.observe(level=0, now=0)
    cell.observe(level=1, now=17)
    assert cell.last_edge == 17
    assert cell.last_polarity == 1
    cell.observe(level=0, now=25)
    assert cell.last_edge == 25
    assert cell.last_polarity == 0


def test_a_steady_input_does_not_move_the_timestamp() -> None:
    cell = model.PinCell()
    cell.observe(level=1, now=4)
    cell.observe(level=1, now=5)
    assert cell.last_edge == 4


def test_the_timebase_wraps_into_the_epoch() -> None:
    base = model.Timebase()
    base.now = model.TIMEBASE_MASK
    base.tick()
    assert base.now == 0
    assert base.epoch == 1
    assert base.wide == 1 << 24


def test_a_deadline_across_the_wrap_still_fires_on_time() -> None:
    cell = model.PinCell()
    base = model.Timebase()
    base.now = model.TIMEBASE_MASK - 4
    due = (base.now + 10) & model.TIMEBASE_MASK
    cell.schedule(due=due, value=1, oe=1, now=base.now)
    fired_at = None
    for _ in range(20):
        base.tick()
        cell.tick(base.now)
        if cell.value and fired_at is None:
            fired_at = base.now
    assert fired_at == due
    assert cell.missed is False
