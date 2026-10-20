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


class Crc:
    """A shared 16-bit CRC/LFSR with a configurable polynomial and direction.

    One bit is shifted per ``CRCBIT`` instruction. Bit-serial rather than
    byte-wide because low-speed USB, which V3 is wired for even though it is
    out of scope for the submission, needs a 5-bit CRC over an 11-bit field
    that no byte-wide unit handles cleanly. The cost is one instruction per
    bit, paid only in the burst at the end of a frame.

    ``msb_first`` selects the shift direction, which also selects where the
    polynomial taps sit:

    - MSB first shifts left and XORs the normal-form polynomial when bit 15
      shifts out. Use for CRC-16/CCITT with poly 0x1021.
    - LSB first shifts right and XORs the reflected polynomial when bit 0
      shifts out. Use for CRC-16/ARC with poly 0xA001 and for USB CRC5.
    """

    def __init__(self) -> None:
        self._poly = 0
        self._msb_first = True
        self._state = 0

    def configure(self, poly: int, msb_first: bool) -> None:
        """Set the polynomial and direction. The state is untouched."""
        self._poly = poly & 0xFFFF
        self._msb_first = msb_first

    def load(self, seed: int) -> None:
        """Load the seed, typically all ones or all zeros."""
        self._state = seed & 0xFFFF

    def shift(self, bit: int) -> None:
        """Shift one message bit through the register."""
        if self._msb_first:
            feedback = ((self._state >> 15) ^ (bit & 1)) & 1
            self._state = (self._state << 1) & 0xFFFF
        else:
            feedback = (self._state ^ (bit & 1)) & 1
            self._state >>= 1
        if feedback:
            self._state ^= self._poly

    @property
    def value(self) -> int:
        """The current 16-bit register contents."""
        return self._state


#: Depth of each per-engine queue, for both the TX bytes and the RX records.
QUEUE_DEPTH = 16


class Queues:
    """One engine's TX byte queue and RX record queue, 16 deep each.

    Behavior is unchanged from V2 (``src/v2_engine.v`` at the ``v2`` tag). Both
    queues are rings addressed by a read and a write pointer, with a separate
    level counter, so wraparound is exercised exactly as in hardware.

    - A TX push is atomic: the whole group of bytes is accepted only if it fits
      (V2 ``tx_ready``: ``tx_level + tx_count <= 16`` and ``tx_count != 0``).
      Otherwise nothing is written and the level is unchanged. Callers detect
      rejection by comparing :attr:`tx_level` before and after.
    - An RX push to a full queue is dropped (V2 ``rx_push`` requires
      ``rx_level < 16``). The engine, not the queue, raises the overflow fault.
    - A pull or pop from an empty queue returns ``None`` and changes nothing.
    - An RX record is ``(value, meta)``: V2 stores ``{word[7:0], isr}``, so
      ``value`` is the 8-bit ISR contents and ``meta`` the 8-bit operand byte
      of the ``RX`` instruction. Both are masked to eight bits.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Empty both queues and return the pointers to zero."""
        self._tx = [0] * QUEUE_DEPTH
        self._rx = [(0, 0)] * QUEUE_DEPTH
        self._tx_rd = self._tx_wr = self._tx_level = 0
        self._rx_rd = self._rx_wr = self._rx_level = 0

    @property
    def tx_level(self) -> int:
        """Number of bytes waiting in the TX queue, 0 to 16."""
        return self._tx_level

    @property
    def rx_level(self) -> int:
        """Number of records waiting in the RX queue, 0 to 16."""
        return self._rx_level

    def push_tx(self, data: bytes) -> None:
        """Append ``data`` to the TX queue if all of it fits, else ignore it.

        An empty ``data`` is ignored, matching V2's ``tx_count != 0`` term.

        V2's host interface limits a push to 1-3 bytes. That limit belongs to
        the caller (the host-interface model), which must enforce it; the
        queue itself enforces only capacity.
        """
        if not data or self._tx_level + len(data) > QUEUE_DEPTH:
            return
        for byte in data:
            self._tx[self._tx_wr] = byte & 0xFF
            self._tx_wr = (self._tx_wr + 1) % QUEUE_DEPTH
        self._tx_level += len(data)

    def pull(self) -> int | None:
        """Remove and return the oldest TX byte, or ``None`` if empty."""
        if self._tx_level == 0:
            return None
        byte = self._tx[self._tx_rd]
        self._tx_rd = (self._tx_rd + 1) % QUEUE_DEPTH
        self._tx_level -= 1
        return byte

    def push_rx(self, value: int, meta: int) -> None:
        """Append an RX record, or drop it if the queue is full."""
        if self._rx_level == QUEUE_DEPTH:
            return
        self._rx[self._rx_wr] = (value & 0xFF, meta & 0xFF)
        self._rx_wr = (self._rx_wr + 1) % QUEUE_DEPTH
        self._rx_level += 1

    def pop_rx(self) -> tuple[int, int] | None:
        """Remove and return the oldest ``(value, meta)`` record, or ``None``."""
        if self._rx_level == 0:
            return None
        record = self._rx[self._rx_rd]
        self._rx_rd = (self._rx_rd + 1) % QUEUE_DEPTH
        self._rx_level -= 1
        return record


#: Records held by :class:`Capture`.
CAPTURE_DEPTH = 32

#: Mask of the shared 32-bit capture timestamp.
CAPTURE_TIMESTAMP_MASK = 0xFFFFFFFF


class CaptureReason:
    """Values of :attr:`Capture.reason`, numbered as in V2.

    V2 also defined ``TIMESTAMP`` (6), set when its private counter saturated.
    V3 has no such condition because capture uses the shared free-running
    timestamp, so the code is kept only to preserve the numbering.
    """

    RESET = 0
    ARMED = 1
    CAPTURING = 2
    STOPPED = 3
    UNTRIGGERED = 4
    CAPACITY = 5
    TIMESTAMP = 6
    DISABLED = 7


class Capture:
    """Triggered capture of pins and engine flags, 32 records deep.

    A port of ``src/v2_capture.v`` (``git show v2:src/v2_capture.v``). Capture
    samples and never controls execution. Call :meth:`arm` once to start, then
    :meth:`record` once per clock with the current sample.

    **What V3 changes from V2.** V2 kept a private counter that restarted at
    zero on arm and stamped each record with cycles since arm. The V3 spec has
    capture use the shared 32-bit timestamp (24-bit timebase plus 8-bit
    epoch), so the caller passes that value in and records carry it unchanged.
    Consequently the timestamp is absolute rather than relative to arm, the
    arm record is no longer stamped zero, and V2's stop-on-timestamp-saturation
    (reason 6) is gone: the shared counter keeps running and wraps at 2**32.

    **No ring wraparound.** The brief mentions wraparound, but V2 has none: the
    record store fills, then capture stops with :attr:`truncated` set and
    reason :attr:`CaptureReason.CAPACITY`, keeping the first 32 records. This
    port does the same; the 33rd sample is never written.

    A record is 64 bits: ``timestamp[63:32]``, ``pins_in[31:24]``,
    ``pins_out[23:16]``, ``pins_oe[15:8]``, then ``flags[7:0]`` with bit 3
    replaced by the trigger annotation (set on the record where the trigger
    fires). Reads past :attr:`count` return zero.

    **Trigger modes** (``trigger_mode``, with ``trigger_mask``):

    - 0: fire immediately.
    - 1: fire when ``pins_in`` changes under the mask. Not evaluated on the
      arming sample, only on later ones.
    - 2: fire when ``flags[1:0]`` has a bit set under the mask.
    - 3: fire when ``flags[2]`` is set.

    Once triggered, a record is taken only when the sample changed, or on the
    trigger itself, or the sample after the trigger, or while ``flags[1:0]``
    is nonzero.
    """

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        """Return every field to its reset value and forget the records."""
        self._records = [0] * CAPTURE_DEPTH
        self.count = 0
        self.trigger_timestamp = 0
        self.armed = False
        self.triggered = False
        self.truncated = False
        self.reason = CaptureReason.RESET
        self._previous = 0
        self._mode = 0
        self._mask = 0
        self._clear_trigger = False

    @staticmethod
    def _sample(pins_in: int, pins_out: int, pins_oe: int, flags: int) -> int:
        """Pack the 32-bit sample; flag bit 3 is reserved for the annotation."""
        return (
            ((pins_in & 0xFF) << 24)
            | ((pins_out & 0xFF) << 16)
            | ((pins_oe & 0xFF) << 8)
            | (flags & 0xF7)
        )

    def _fire(self, mode: int, mask: int, sample: int, arming: bool) -> bool:
        flags = sample & 0xFF
        if self.triggered and not arming:
            return False
        if mode == 0:
            return True
        if mode == 1:
            return not arming and bool(((sample >> 24) ^ (self._previous >> 24)) & mask)
        if mode == 2:
            return bool(flags & mask & 0x03)
        return bool(flags & 0x04)

    def _store(self, index: int, timestamp: int, sample: int, fire: bool) -> None:
        self._records[index] = (
            ((timestamp & CAPTURE_TIMESTAMP_MASK) << 32)
            | sample
            | (0x08 if fire else 0)
        )

    def arm(
        self,
        timestamp: int,
        trigger_mode: int,
        trigger_mask: int,
        pins_in: int,
        pins_out: int,
        pins_oe: int,
        flags: int,
    ) -> None:
        """Start a new capture, discarding the previous one.

        The arming sample is stored as record 0 if the trigger fires on it.
        """
        trigger_mode &= 3
        trigger_mask &= 0xFF
        sample = self._sample(pins_in, pins_out, pins_oe, flags)
        fire = self._fire(trigger_mode, trigger_mask, sample, arming=True)
        if fire:
            self._store(0, timestamp, sample, fire)
        self._mode = trigger_mode
        self._mask = trigger_mask
        self._previous = sample
        self.trigger_timestamp = timestamp & CAPTURE_TIMESTAMP_MASK if fire else 0
        self.triggered = fire
        self.count = 1 if fire else 0
        self._clear_trigger = fire
        self.armed = True
        self.truncated = False
        self.reason = CaptureReason.CAPTURING if fire else CaptureReason.ARMED

    def record(
        self,
        timestamp: int,
        pins_in: int,
        pins_out: int,
        pins_oe: int,
        flags: int,
        stop: bool = False,
        disable: bool = False,
    ) -> bool:
        """Advance one clock with the current sample; return whether it was stored.

        Does nothing unless armed. ``disable`` outranks ``stop``; either ends
        the capture without taking a sample.
        """
        if not self.armed:
            return False
        if disable:
            self.armed = False
            self.reason = CaptureReason.DISABLED
            return False
        if stop:
            self.armed = False
            self.reason = (
                CaptureReason.STOPPED if self.triggered else CaptureReason.UNTRIGGERED
            )
            return False
        sample = self._sample(pins_in, pins_out, pins_oe, flags)
        fire = self._fire(self._mode, self._mask, sample, arming=False)
        take = (self.triggered or fire) and (
            fire or sample != self._previous or self._clear_trigger or bool(sample & 0x03)
        )
        if take:
            self._store(self.count, timestamp, sample, fire)
        self._previous = sample
        self._clear_trigger = fire
        if fire:
            self.triggered = True
            self.trigger_timestamp = timestamp & CAPTURE_TIMESTAMP_MASK
            self.reason = CaptureReason.CAPTURING
        if take:
            self.count += 1
            if self.count == CAPTURE_DEPTH:
                self.armed = False
                self.truncated = True
                self.reason = CaptureReason.CAPACITY
        return take

    def read(self, index: int) -> int:
        """Return the 64-bit record at ``index``, or zero if not yet written."""
        if 0 <= index < self.count:
            return self._records[index]
        return 0
