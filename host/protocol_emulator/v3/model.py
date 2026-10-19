"""Cycle-accurate model of the V3 design.

This model is the reference the RTL is compared against on every clock edge.
It is written to be read: where a choice exists between a fast implementation
and one that mirrors the hardware structure, it mirrors the hardware.
"""
from typing import NamedTuple

from . import PIN_COUNT, PROGRAM_WORDS, TIMEBASE_BITS

TIMEBASE_MASK = (1 << TIMEBASE_BITS) - 1
TIMEBASE_HALF = 1 << (TIMEBASE_BITS - 1)


class SlotBusy(RuntimeError):
    """A commit targeted a pin whose pending slot is still occupied.

    In hardware this is not an error: the engine stalls. The model raises so
    that callers which have not checked :meth:`PinCell.free` first are caught.
    """


def signed_distance(due: int, now: int) -> int:
    """Signed 24-bit distance from ``now`` to ``due``.

    Positive means the deadline is in the future, zero means it is this cycle,
    negative means it has already passed. The comparison is modular, so it is
    correct across the 349.5 ms counter wrap as long as the true distance stays
    inside +/-8,388,608 ticks.
    """
    return ((due - now + TIMEBASE_HALF) & TIMEBASE_MASK) - TIMEBASE_HALF


class Pending(NamedTuple):
    """The single pending commit held by a pin cell."""

    due: int
    value: int
    oe: int


class PinCell:
    """One protocol pin: output scheduling and input edge timestamping.

    The cell holds at most one pending commit. The owning engine stalls rather
    than overwriting an occupied slot, which throttles the engine to the pin's
    event rate without a separate synchronization instruction.
    """

    def __init__(self, open_drain: bool = False) -> None:
        self.open_drain = open_drain
        self.value = 0
        self.oe = 0
        self.pending: Pending | None = None
        self.missed = False
        self.last_edge = 0
        self.last_polarity = 0
        self._level = 0

    @property
    def free(self) -> bool:
        """Whether a new commit can be accepted this cycle."""
        return self.pending is None

    def schedule(self, due: int, value: int, oe: int, now: int) -> None:
        """Schedule ``(value, oe)`` to apply at ``due``.

        A deadline already in the past applies immediately and sets the sticky
        missed flag; the engine is not stalled and the edge is not dropped.
        """
        if self.pending is not None:
            raise SlotBusy("pin cell slot is occupied")
        if signed_distance(due, now) <= 0:
            if signed_distance(due, now) < 0:
                self.missed = True
            self._apply(value, oe)
            return
        self.pending = Pending(due & TIMEBASE_MASK, value, oe)

    def tick(self, now: int) -> None:
        """Advance one cycle, applying the pending commit if it is due."""
        if self.pending is not None and signed_distance(self.pending.due, now) <= 0:
            self._apply(self.pending.value, self.pending.oe)
            self.pending = None

    def _apply(self, value: int, oe: int) -> None:
        self.value = value & 1
        self.oe = oe & 1

    def drive(self) -> tuple[int, int]:
        """Return the ``(value, oe)`` actually presented to the pad.

        An open-drain pin asked to drive high is released instead, so the
        external pull-up defines the high level and no bus contention is
        possible.
        """
        if self.open_drain and self.value:
            return (0, 0)
        return (self.value, self.oe)

    def observe(self, level: int, now: int) -> None:
        """Record a synchronized input level, timestamping any edge."""
        level &= 1
        if level != self._level:
            self.last_edge = now & TIMEBASE_MASK
            self.last_polarity = level
        self._level = level

    @property
    def level(self) -> int:
        """The last observed synchronized input level."""
        return self._level

    def clear_missed(self) -> None:
        """Clear the sticky missed-deadline flag."""
        self.missed = False


class Timebase:
    """The shared free-running counter and its epoch extension."""

    def __init__(self) -> None:
        self.now = 0
        self.epoch = 0

    def tick(self) -> None:
        """Advance one clock cycle, carrying into the epoch on wrap."""
        self.now = (self.now + 1) & TIMEBASE_MASK
        if self.now == 0:
            self.epoch = (self.epoch + 1) & 0xFF

    @property
    def wide(self) -> int:
        """The 32-bit timestamp used by capture and host readout."""
        return (self.epoch << TIMEBASE_BITS) | self.now
