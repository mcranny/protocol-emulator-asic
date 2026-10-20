"""Cycle-accurate model of the V3 design.

This model is the reference the RTL is compared against on every clock edge.
It is written to be read: where a choice exists between a fast implementation
and one that mirrors the hardware structure, it mirrors the hardware.
"""
from typing import Callable, NamedTuple

from . import PIN_COUNT, PROGRAM_WORDS, TIMEBASE_BITS, isa
from .opcodes import CONDITIONS, POLARITIES

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


class Synchronizer:
    """A two-flop input synchronizer, eight pins wide.

    Pad inputs are asynchronous to the 48 MHz clock, so each is clocked through
    two flip-flops before anything downstream uses it. A level presented at the
    pad appears at the output two cycles later. The eight bits are independent;
    one byte-wide instance is exactly eight per-pin synchronizers run in step,
    which is how the top level instantiates them.

    :meth:`tick` follows a read-then-advance convention: it returns the output
    valid on the current cycle, then clocks the edge. The raw synchronized byte
    it returns is what capture records (as in V2); :class:`InputFilter` consumes
    it to produce the engine-visible level.
    """

    def __init__(self) -> None:
        self._first = 0
        self._second = 0

    def tick(self, raw: int) -> int:
        """Clock one cycle; return the synchronized byte valid this cycle."""
        out = self._second
        self._second = self._first
        self._first = raw & 0xFF
        return out

    @property
    def value(self) -> int:
        """The synchronized byte currently presented at the output."""
        return self._second


class InputFilter:
    """A three-sample debounce filter, eight pins wide.

    A direct port of ``src/v2_input_filter.v`` (``git show v2:src/v2_input_filter.v``):
    ``filtered = (s & p & o) | (filtered & (s | p | o))`` where ``s``, ``p`` and
    ``o`` are the current sample and the two before it. Three consecutive
    agreeing samples qualify a change, so a clean edge appears up to three cycles
    after it reaches the filter input and glitches shorter than that are
    rejected. The eight bits are independent, matching the eight per-pin filters
    the V3 top level instantiates.

    Fed from :class:`Synchronizer`, the combined pad-to-filtered latency is about
    two synchronizer cycles plus up to three filter cycles. Unlike V2, V3 filters
    all eight pins unconditionally: there is no filter mask (the V3 loader has no
    command for one), so the engine-visible level is always the debounced value.

    :meth:`tick` uses the same read-then-advance convention as
    :class:`Synchronizer`: it returns the filtered byte valid this cycle, then
    clocks the edge.
    """

    def __init__(self) -> None:
        self._previous = 0
        self._older = 0
        self._filtered = 0

    def tick(self, samples: int) -> int:
        """Clock one cycle; return the filtered byte valid this cycle."""
        samples &= 0xFF
        out = self._filtered
        self._filtered = (samples & self._previous & self._older) | (
            self._filtered & (samples | self._previous | self._older)
        )
        self._older = self._previous
        self._previous = samples
        return out

    @property
    def value(self) -> int:
        """The filtered (engine-visible) byte currently at the output."""
        return self._filtered


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


#: Registers (R0-R3) and the cursor are 24 bits wide.
REGISTER_BITS = 24
REGISTER_MASK = (1 << REGISTER_BITS) - 1

#: Depth of the per-engine return stack.
RETURN_STACK_DEPTH = 2

#: Error bits, mirrored by ``E_*`` localparams in ``src/v3_engine.v``.
E_ILLEGAL = 0x0001
E_INVALID_WORD = 0x0002
E_STACK = 0x0004
E_OWNERSHIP = 0x0008
E_FIRMWARE = 0x0010
E_QUEUE = 0x0020
E_TIMEOUT = 0x0040


class NotLoaded(RuntimeError):
    """START was attempted before every program word was written."""


class ProgramMemory:
    """256x32 program store with a registered read and a validity bitmap.

    SRAM contents are undefined at power-up, so a word that has never been
    written must not be executed. The bitmap makes that a detectable fault
    rather than undefined behaviour, and START is refused until it is full.
    """

    def __init__(self) -> None:
        self._words = [0] * PROGRAM_WORDS
        self._written = [False] * PROGRAM_WORDS

    def write(self, addr: int, word: int) -> None:
        """Write ``word`` at ``addr`` and mark the address as valid.

        :raises ValueError: if ``addr`` is outside ``0..PROGRAM_WORDS - 1``.
        """
        if not 0 <= addr < PROGRAM_WORDS:
            raise ValueError(f"address {addr} is outside 0..{PROGRAM_WORDS - 1}")
        self._words[addr] = word & 0xFFFFFFFF
        self._written[addr] = True

    def read(self, addr: int) -> int:
        """Return the word at ``addr`` (wrapped into range)."""
        return self._words[addr & (PROGRAM_WORDS - 1)]

    def is_written(self, addr: int) -> bool:
        """Return whether ``addr`` has ever been written."""
        return self._written[addr & (PROGRAM_WORDS - 1)]

    @property
    def valid(self) -> bool:
        """Whether every word has been written. START requires this."""
        return all(self._written)

    def reset(self) -> None:
        """Deselection clears the bitmap; the contents are no longer trusted."""
        self._written = [False] * PROGRAM_WORDS


#: One instruction handler: ``(operands, now) -> action``.
Handler = Callable[[dict[str, int], int], str]

#: Action returned by an instruction handler.
#:
#: ``"next"``
#:     Retire and prefetch ``pc + 1``.
#: ``"redirect"``
#:     The handler set ``pc`` to a branch target; insert a bubble and fetch it.
#: ``"stall"``
#:     Hold the engine on this instruction and retry next cycle.
#: ``"fault"``
#:     The handler raised an engine fault and halted; the instruction does not
#:     retire.
_ACTION_NEXT = "next"
_ACTION_REDIRECT = "redirect"
_ACTION_STALL = "stall"
_ACTION_FAULT = "fault"


class Engine:
    """One programmable engine: fetch pipeline, cursor, registers and commits.

    The engine fetches from its own :class:`ProgramMemory`, executes one
    instruction per cycle on straight-line code, and schedules pin changes on
    the shared timebase. The fetch is registered: the address presented on one
    cycle delivers its word on the next, so a taken branch always costs two
    cycles. The engine owns only the pins named by ``owner`` and stalls rather
    than overwriting an occupied pin slot.

    :ivar retired: instructions retired since START. An instruction counts when
        it runs to completion, including ``HALT`` and ``FAIL`` (both deliberate
        terminators). An instruction that raises an engine fault does not count:
        ``E_ILLEGAL`` and ``E_INVALID_WORD`` are caught before execution, and
        ``E_STACK`` (``CALL``/``RET``) and ``E_OWNERSHIP`` (a commit) abandon
        the instruction without retiring it.
    """

    def __init__(
        self,
        pins: list[PinCell],
        owner: int,
        open_drain: int = 0,
        crc: "Crc | None" = None,
    ) -> None:
        """Create an engine over the shared ``pins`` with the given masks.

        :param pins: the eight shared pin cells; the engine writes only those
            it owns.
        :param owner: 8-bit mask of pins this engine may commit to.
        :param open_drain: 8-bit open-drain mask, recorded for completeness.
        :param crc: the shared :class:`Crc` unit. Both engines are given the
            same instance so their CRC bursts chain; stepping engine 0 before
            engine 1 each cycle makes the order deterministic. When ``None`` a
            private unit is created, which suits a single-engine test.

        The queues are owned per engine (:attr:`queues`). Edge watchers are held
        in :attr:`watches` as ``(mask, value, edges)`` tuples with
        :attr:`abort_mask` naming which of them abort a wait; both are set
        directly before :meth:`start`. :attr:`events`, :attr:`previous`,
        :attr:`marker`, :attr:`pull_empty` and :attr:`isr` carry the event,
        edge-detection, capture-marker, pull and shift-register state.
        """
        self.pins = pins
        self.owner = owner
        self.open_drain = open_drain
        self.memory = ProgramMemory()
        self.queues = Queues()
        self.crc = crc if crc is not None else Crc()

        self.pc = 0
        self.cursor = 0
        self.regs = [0, 0, 0, 0]
        self.isr = 0

        #: Per-watcher event bits, set when a watcher's pattern and edge match.
        self.events = 0
        #: Watcher configuration, up to two ``(mask, value, edges)`` tuples.
        self.watches: list[tuple[int, int, int]] = []
        #: Mask of watchers (bit i = watcher i) that abort a WAITEDGE/WAITFOR.
        self.abort_mask = 0
        #: Engine-visible pin byte from the previous cycle, for edge detection.
        self.previous = 0
        #: One-cycle capture marker set by MARK, cleared at the top of ``step``.
        self.marker = 0
        #: Sticky result of the last PULLNB: true when the TX queue was empty.
        self.pull_empty = False

        self.running = False
        self.halted = False
        self.stalled = False
        self.errors = 0
        self.fault_code = 0
        self.retired = 0
        self.cycles_to_halt = 0

        self._fetched = 0
        self._fetch_addr = 0
        self._bubble = False
        self._stack: list[int] = []

        self._flag_timeout = False
        self._flag_interrupted = False
        self._flag_zero = False
        self._flag_carry = False

        self._waitedge_active = False
        self._waitedge_baseline = 0

        self._handlers: dict[str, Handler] = {
            "NOP": self._op_nop,
            "HALT": self._op_halt,
            "JMP": self._op_jmp,
            "BRC": self._op_brc,
            "DJNZ": self._op_djnz,
            "CALL": self._op_call,
            "RET": self._op_ret,
            "FAIL": self._op_fail,
            "LI": self._op_li,
            "MOV": self._op_mov,
            "ADD": self._op_add,
            "SUB": self._op_sub,
            "AND": self._op_and,
            "OR": self._op_or,
            "XOR": self._op_xor,
            "SHL": self._op_shl,
            "SHR": self._op_shr,
            "BREQ": self._op_breq,
            "ADVANCE": self._op_advance,
            "ADVANCER": self._op_advancer,
            "CURSORSET": self._op_cursorset,
            "CURSORGET": self._op_cursorget,
            "NOW": self._op_now,
            "WAITTIME": self._op_waittime,
            "SET": self._op_set,
            "OE": self._op_oe,
            "DRIVE": self._op_drive,
            "OUT": self._op_out,
            "SAMPLE": self._op_sample,
            "IN": self._op_in,
            "EDGETIME": self._op_edgetime,
            "WAITEDGE": self._op_waitedge,
            "WAITFOR": self._op_waitfor,
            "MOVIS": self._op_movis,
            "CLEARIS": self._op_clearis,
            "PULL": self._op_pull,
            "PULLNB": self._op_pullnb,
            "RX": self._op_rx,
            "MARK": self._op_mark,
            "WAITEVENT": self._op_waitevent,
            "CLEAREVENT": self._op_clearevent,
            "CLEARMISS": self._op_clearmiss,
            "CRCCFG": self._op_crccfg,
            "CRCLOAD": self._op_crcload,
            "CRCBIT": self._op_crcbit,
            "CRCGET": self._op_crcget,
        }

    # -- lifecycle ------------------------------------------------------

    def load(self, image: "isa.Image") -> None:
        """Write every word of ``image`` into program memory.

        The assembler fills unused addresses with ``HALT``, so all 256 words
        are written and :attr:`ProgramMemory.valid` becomes true.
        """
        for addr, word in enumerate(image.words):
            self.memory.write(addr, word)

    def start(self, now: int) -> None:
        """Begin execution with the cursor initialised to ``now``.

        :raises NotLoaded: if any program word has not been written.
        """
        if not self.memory.valid:
            raise NotLoaded("START requires every program word to be written")
        self.cursor = now & TIMEBASE_MASK
        self.pc = 0
        self.running = True
        self.halted = False
        self.stalled = False
        self.retired = 0
        self.cycles_to_halt = 0
        self.events = 0
        self.marker = 0
        self.pull_empty = False
        self._bubble = False
        self._stack = []
        self._waitedge_active = False
        self._fetch(0)

    def stop(self) -> None:
        """Halt fetching without raising an error. Ownership may then change."""
        self.running = False

    def step(self, now: int) -> None:
        """Advance one clock cycle.

        Executes the word delivered by the previous cycle's fetch, resolves any
        stall, and prefetches the next word. A taken branch inserts a one-cycle
        bubble so it costs exactly two cycles.

        The one-cycle MARK marker is cleared and the edge watchers are evaluated
        first, on every clock, so they track the pins independently of the fetch
        pipeline exactly as the hardware does.
        """
        self.marker = 0
        self._evaluate_watchers()
        if not self.running:
            return
        self.cycles_to_halt += 1

        if self._bubble:
            self._bubble = False
            self.stalled = False
            return

        try:
            instr = isa.decode(self._fetched)
        except isa.IllegalInstruction:
            self._fault(E_ILLEGAL)
            return
        if not self.memory.is_written(self._fetch_addr):
            self._fault(E_INVALID_WORD)
            return

        action = self._execute(instr, now)
        if action == _ACTION_STALL:
            self.stalled = True
            return
        self.stalled = False
        if action == _ACTION_FAULT:
            return
        self.retired += 1
        if self.halted:
            return
        if action == _ACTION_REDIRECT:
            self._bubble = True
            self._fetch(self.pc)
        else:
            self.pc = (self.pc + 1) & (PROGRAM_WORDS - 1)
            self._fetch(self.pc)

    # -- internals ------------------------------------------------------

    def _fetch(self, addr: int) -> None:
        self._fetch_addr = addr & (PROGRAM_WORDS - 1)
        self._fetched = self.memory.read(self._fetch_addr)

    def _fault(self, bit: int) -> None:
        self.errors |= bit
        self.running = False
        self.halted = True
        self.stalled = False

    def _execute(self, instr: "isa.Instruction", now: int) -> str:
        return self._handlers[instr.name](instr.operands, now)

    def _condition(self, cond: int) -> bool:
        if cond == CONDITIONS["timeout"]:
            return self._flag_timeout
        if cond == CONDITIONS["interrupted"]:
            return self._flag_interrupted
        if cond == CONDITIONS["missed"]:
            return any(cell.missed for cell in self.pins)
        if cond == CONDITIONS["tx_empty"]:
            return self.queues.tx_level == 0
        if cond == CONDITIONS["rx_full"]:
            return self.queues.rx_level == QUEUE_DEPTH
        if cond == CONDITIONS["pull_empty"]:
            return self.pull_empty
        if cond == CONDITIONS["event0"]:
            return bool(self.events & 1)
        if cond == CONDITIONS["event1"]:
            return bool(self.events & 2)
        if cond == CONDITIONS["zero"]:
            return self._flag_zero
        if cond == CONDITIONS["carry"]:
            return self._flag_carry
        raise AssertionError(f"unhandled BRC condition {cond}")

    def _pins_byte(self) -> int:
        """Return the engine-visible (filtered) levels of all eight pins."""
        return sum((self.pins[index].level & 1) << index for index in range(PIN_COUNT))

    def _evaluate_watchers(self) -> None:
        """Set each watcher's event bit on a qualifying masked pattern and edge.

        Mirrors V2 ``Engine.tick``: watcher ``i`` sets ``events`` bit ``i`` when
        the engine-visible pins match its value under its mask and a watched edge
        occurs since the previous cycle. Event bits are sticky until CLEAREVENT.
        """
        pins = self._pins_byte()
        for index, (mask, value, edges) in enumerate(self.watches):
            if (pins & mask) == value and ((pins ^ self.previous) & edges):
                self.events |= 1 << index
        self.previous = pins

    # -- control --------------------------------------------------------

    def _op_nop(self, ops: dict[str, int], now: int) -> str:
        return _ACTION_NEXT

    def _op_halt(self, ops: dict[str, int], now: int) -> str:
        self.running = False
        self.halted = True
        return _ACTION_NEXT

    def _op_jmp(self, ops: dict[str, int], now: int) -> str:
        self.pc = ops["target"]
        return _ACTION_REDIRECT

    def _op_brc(self, ops: dict[str, int], now: int) -> str:
        if self._condition(ops["condition"]):
            self.pc = ops["target"]
            return _ACTION_REDIRECT
        return _ACTION_NEXT

    def _op_breq(self, ops: dict[str, int], now: int) -> str:
        if self.regs[ops["reg"]] == ops["value"]:
            self.pc = ops["target"]
            return _ACTION_REDIRECT
        return _ACTION_NEXT

    def _op_djnz(self, ops: dict[str, int], now: int) -> str:
        reg = ops["reg"]
        self.regs[reg] = (self.regs[reg] - 1) & REGISTER_MASK
        if self.regs[reg] != 0:
            self.pc = ops["target"]
            return _ACTION_REDIRECT
        return _ACTION_NEXT

    def _op_call(self, ops: dict[str, int], now: int) -> str:
        if len(self._stack) >= RETURN_STACK_DEPTH:
            self._fault(E_STACK)
            return _ACTION_FAULT
        self._stack.append((self.pc + 1) & (PROGRAM_WORDS - 1))
        self.pc = ops["target"]
        return _ACTION_REDIRECT

    def _op_ret(self, ops: dict[str, int], now: int) -> str:
        if not self._stack:
            self._fault(E_STACK)
            return _ACTION_FAULT
        self.pc = self._stack.pop()
        return _ACTION_REDIRECT

    def _op_fail(self, ops: dict[str, int], now: int) -> str:
        self.errors |= E_FIRMWARE
        self.fault_code = ops["code"]
        self.running = False
        self.halted = True
        return _ACTION_NEXT

    # -- registers and ALU ---------------------------------------------

    def _op_li(self, ops: dict[str, int], now: int) -> str:
        self.regs[ops["reg"]] = ops["value"] & REGISTER_MASK
        return _ACTION_NEXT

    def _op_mov(self, ops: dict[str, int], now: int) -> str:
        self.regs[ops["dst"]] = self.regs[ops["src"]]
        return _ACTION_NEXT

    def _op_add(self, ops: dict[str, int], now: int) -> str:
        """``dst += src``; carry is the carry-out of the 24-bit add, zero the
        result."""
        total = self.regs[ops["dst"]] + self.regs[ops["src"]]
        self.regs[ops["dst"]] = total & REGISTER_MASK
        self._flag_carry = total > REGISTER_MASK
        self._flag_zero = self.regs[ops["dst"]] == 0
        return _ACTION_NEXT

    def _op_sub(self, ops: dict[str, int], now: int) -> str:
        """``dst -= src``; carry is the borrow (set when ``dst < src``), zero
        the result."""
        diff = self.regs[ops["dst"]] - self.regs[ops["src"]]
        self._flag_carry = diff < 0
        self.regs[ops["dst"]] = diff & REGISTER_MASK
        self._flag_zero = self.regs[ops["dst"]] == 0
        return _ACTION_NEXT

    def _op_and(self, ops: dict[str, int], now: int) -> str:
        """``dst &= src``; logical ops set zero only and leave carry unchanged."""
        self.regs[ops["dst"]] &= self.regs[ops["src"]]
        self._flag_zero = self.regs[ops["dst"]] == 0
        return _ACTION_NEXT

    def _op_or(self, ops: dict[str, int], now: int) -> str:
        """``dst |= src``; logical ops set zero only and leave carry unchanged."""
        self.regs[ops["dst"]] |= self.regs[ops["src"]]
        self._flag_zero = self.regs[ops["dst"]] == 0
        return _ACTION_NEXT

    def _op_xor(self, ops: dict[str, int], now: int) -> str:
        """``dst ^= src``; logical ops set zero only and leave carry unchanged."""
        self.regs[ops["dst"]] ^= self.regs[ops["src"]]
        self._flag_zero = self.regs[ops["dst"]] == 0
        return _ACTION_NEXT

    def _op_shl(self, ops: dict[str, int], now: int) -> str:
        """``dst <<= amount``; carry is the last bit shifted out past bit 23,
        zero the masked result. A shift of 24 or more shifts every bit out, so
        carry is the bit that lands on bit 24 (0 once the shift clears it) and
        the result is zero."""
        dst = ops["dst"]
        amount = ops["amount"]
        shifted = self.regs[dst] << amount
        self.regs[dst] = shifted & REGISTER_MASK
        self._flag_carry = bool((shifted >> REGISTER_BITS) & 1)
        self._flag_zero = self.regs[dst] == 0
        return _ACTION_NEXT

    def _op_shr(self, ops: dict[str, int], now: int) -> str:
        """``dst >>= amount``; carry is the last bit shifted out (bit
        ``amount - 1`` of the original, 0 when ``amount`` is 0), zero the
        result. A shift of 24 or more shifts every bit out, leaving zero and a
        carry taken from an already-cleared register (0)."""
        dst = ops["dst"]
        amount = ops["amount"]
        value = self.regs[dst]
        self._flag_carry = bool((value >> (amount - 1)) & 1) if amount > 0 else False
        self.regs[dst] = (value >> amount) & REGISTER_MASK
        self._flag_zero = self.regs[dst] == 0
        return _ACTION_NEXT

    # -- time and cursor -----------------------------------------------

    def _op_advance(self, ops: dict[str, int], now: int) -> str:
        self.cursor = (self.cursor + ops["ticks"]) & TIMEBASE_MASK
        return _ACTION_NEXT

    def _op_advancer(self, ops: dict[str, int], now: int) -> str:
        self.cursor = (self.cursor + self.regs[ops["reg"]]) & TIMEBASE_MASK
        return _ACTION_NEXT

    def _op_cursorset(self, ops: dict[str, int], now: int) -> str:
        self.cursor = self.regs[ops["reg"]] & TIMEBASE_MASK
        return _ACTION_NEXT

    def _op_cursorget(self, ops: dict[str, int], now: int) -> str:
        self.regs[ops["reg"]] = self.cursor & REGISTER_MASK
        return _ACTION_NEXT

    def _op_now(self, ops: dict[str, int], now: int) -> str:
        self.regs[ops["reg"]] = now & TIMEBASE_MASK
        return _ACTION_NEXT

    def _op_waittime(self, ops: dict[str, int], now: int) -> str:
        if signed_distance(self.cursor, now) > 0:
            return _ACTION_STALL
        return _ACTION_NEXT

    # -- pin commits ----------------------------------------------------

    def _op_out(self, ops: dict[str, int], now: int) -> str:
        pin = ops["pin"]
        return self._commit([(pin, ops["level"], 1)], [pin], ops["offset"], now)

    def _op_set(self, ops: dict[str, int], now: int) -> str:
        mask = ops["mask"]
        value = ops["value"]
        targets = [
            (i, (value >> i) & 1, self.pins[i].oe)
            for i in range(PIN_COUNT)
            if (mask >> i) & 1
        ]
        require_own = [i for i in range(PIN_COUNT) if (mask >> i) & 1]
        return self._commit(targets, require_own, ops["offset"], now)

    def _op_oe(self, ops: dict[str, int], now: int) -> str:
        mask = ops["mask"]
        oe = ops["oe"]
        targets = [
            (i, self.pins[i].value, (oe >> i) & 1)
            for i in range(PIN_COUNT)
            if (mask >> i) & 1
        ]
        require_own = [i for i in range(PIN_COUNT) if (mask >> i) & 1]
        return self._commit(targets, require_own, ops["offset"], now)

    def _op_drive(self, ops: dict[str, int], now: int) -> str:
        """Drive every owned pin to ``value[i]``/``oe[i]`` at ``cursor + offset``.

        DRIVE has no mask. It writes only the pins this engine owns: an unowned
        pin is never written and never stalled on, and its ``value`` bit is
        ignored. Enabling an unowned pin is illegal, so if any ``oe`` bit names
        an unowned pin the commit sets ``E_OWNERSHIP`` and schedules nothing on
        any pin. With ``owner = 0xFF`` this drives all eight pins.
        """
        value = ops["value"]
        oe = ops["oe"]
        for i in range(PIN_COUNT):
            if (oe >> i) & 1 and not (self.owner >> i) & 1:
                self._fault(E_OWNERSHIP)
                return _ACTION_FAULT
        targets = [
            (i, (value >> i) & 1, (oe >> i) & 1)
            for i in range(PIN_COUNT)
            if (self.owner >> i) & 1
        ]
        return self._commit(targets, [], ops["offset"], now)

    def _commit(
        self,
        targets: list[tuple[int, int, int]],
        require_own: list[int],
        offset: int,
        now: int,
    ) -> str:
        """Schedule a group of pin changes, faulting or stalling as required.

        :param targets: ``(pin, value, oe)`` triples to schedule together.
        :param require_own: pins that must be owned for the commit to be legal.
        :param offset: ticks added to the cursor for the due time.
        :returns: ``"next"`` when scheduled, ``"fault"`` when a required pin is
            unowned (``E_OWNERSHIP``, nothing scheduled), ``"stall"`` when a
            target pin's slot is still occupied.
        """
        for pin in require_own:
            if not (self.owner >> pin) & 1:
                self._fault(E_OWNERSHIP)
                return _ACTION_FAULT
        for pin, _value, _oe in targets:
            if not self.pins[pin].free:
                return _ACTION_STALL
        due = (self.cursor + offset) & TIMEBASE_MASK
        for pin, value, oe in targets:
            self.pins[pin].schedule(due, value, oe, now)
        return _ACTION_NEXT

    # -- inputs ---------------------------------------------------------

    def _op_sample(self, ops: dict[str, int], now: int) -> str:
        """``regs[reg]`` = the eight engine-visible levels, upper bits zero."""
        self.regs[ops["reg"]] = self._pins_byte() & REGISTER_MASK
        return _ACTION_NEXT

    def _op_in(self, ops: dict[str, int], now: int) -> str:
        """Shift one pin bit into the 8-bit ISR, MSB or LSB first."""
        bit = self.pins[ops["pin"]].level & 1
        if ops["msb_first"]:
            self.isr = ((self.isr << 1) | bit) & 0xFF
        else:
            self.isr = (self.isr >> 1) | (bit << 7)
        return _ACTION_NEXT

    def _op_edgetime(self, ops: dict[str, int], now: int) -> str:
        """``regs[reg]`` = the timestamp of the pin's last filtered edge."""
        self.regs[ops["reg"]] = self.pins[ops["pin"]].last_edge & REGISTER_MASK
        return _ACTION_NEXT

    def _op_waitedge(self, ops: dict[str, int], now: int) -> str:
        """Stall until a qualifying timestamped edge, a timeout or an abort.

        On the first cycle a baseline ``b = now`` is latched. An edge qualifies
        when ``signed_distance(last_edge, b) >= 0`` (an edge recorded on the
        entry cycle counts) and its polarity matches. The live level is never
        read. Ends by abort, match or timeout; see :meth:`_finish_abortable_wait`.
        """
        if not self._waitedge_active:
            self._waitedge_active = True
            self._waitedge_baseline = now & TIMEBASE_MASK
        cell = self.pins[ops["pin"]]
        fresh_edge = signed_distance(cell.last_edge, self._waitedge_baseline) >= 0
        polarity = ops["polarity"]
        if polarity == POLARITIES["rising"]:
            polarity_ok = cell.last_polarity == 1
        elif polarity == POLARITIES["falling"]:
            polarity_ok = cell.last_polarity == 0
        else:
            polarity_ok = True
        return self._finish_abortable_wait(fresh_edge and polarity_ok, now)

    def _op_waitfor(self, ops: dict[str, int], now: int) -> str:
        """Stall until the pin's engine-visible level matches, else timeout/abort."""
        matched = self.pins[ops["pin"]].level == ops["level"]
        return self._finish_abortable_wait(matched, now)

    def _finish_abortable_wait(self, matched: bool, now: int) -> str:
        """Resolve a WAITEDGE/WAITFOR, setting exactly one outcome flag.

        Same-cycle priority is interrupt > match > timeout, as in the V2 model
        (``self.timeout = not matched and not aborted; self.interrupted =
        aborted``): an abort-enabled event outranks a match, a match outranks the
        cursor-derived timeout. A match clears both flags, an abort sets
        interrupt and clears timeout, a timeout sets timeout and clears interrupt.
        """
        aborted = (self.events & self.abort_mask) != 0
        timed_out = signed_distance(self.cursor, now) <= 0
        if aborted:
            self._flag_interrupted = True
            self._flag_timeout = False
        elif matched:
            self._flag_interrupted = False
            self._flag_timeout = False
        elif timed_out:
            self._flag_timeout = True
            self._flag_interrupted = False
        else:
            return _ACTION_STALL
        self._waitedge_active = False
        return _ACTION_NEXT

    def _op_movis(self, ops: dict[str, int], now: int) -> str:
        """``regs[reg]`` = the 8-bit ISR, zero-extended."""
        self.regs[ops["reg"]] = self.isr & REGISTER_MASK
        return _ACTION_NEXT

    def _op_clearis(self, ops: dict[str, int], now: int) -> str:
        """Clear the ISR to zero."""
        self.isr = 0
        return _ACTION_NEXT

    # -- queues ---------------------------------------------------------

    def _op_pull(self, ops: dict[str, int], now: int) -> str:
        """Pop the oldest TX byte into ``regs[reg]``, stalling while empty.

        This is the one unbounded, host-dependent stall: an empty TX queue holds
        the engine until the host pushes a byte. It does not touch ``pull_empty``.
        """
        byte = self.queues.pull()
        if byte is None:
            return _ACTION_STALL
        self.regs[ops["reg"]] = byte & REGISTER_MASK
        return _ACTION_NEXT

    def _op_pullnb(self, ops: dict[str, int], now: int) -> str:
        """Non-blocking pull: set ``pull_empty`` and pop a byte if one is there."""
        self.pull_empty = self.queues.tx_level == 0
        byte = self.queues.pull()
        if byte is not None:
            self.regs[ops["reg"]] = byte & REGISTER_MASK
        return _ACTION_NEXT

    def _op_rx(self, ops: dict[str, int], now: int) -> str:
        """Push ``(isr, meta)`` onto the RX queue; a full queue faults ``E_QUEUE``."""
        if self.queues.rx_level == QUEUE_DEPTH:
            self._fault(E_QUEUE)
            return _ACTION_FAULT
        self.queues.push_rx(value=self.isr, meta=ops["meta"])
        return _ACTION_NEXT

    # -- events ---------------------------------------------------------

    def _op_mark(self, ops: dict[str, int], now: int) -> str:
        """Set the one-cycle capture marker to ``code``."""
        self.marker = ops["code"] & 0xFF
        return _ACTION_NEXT

    def _op_waitevent(self, ops: dict[str, int], now: int) -> str:
        """Stall until any masked event bit is set, else time out on the cursor.

        WAITEVENT is never aborted (as in V2). A match clears both wait flags; a
        cursor-derived timeout sets ``timeout`` and clears ``interrupted``. The
        timeout keeps PULL on an empty TX queue as the only unbounded stall.
        """
        if self.events & ops["mask"]:
            self._flag_timeout = False
            self._flag_interrupted = False
            return _ACTION_NEXT
        if signed_distance(self.cursor, now) <= 0:
            self._flag_timeout = True
            self._flag_interrupted = False
            return _ACTION_NEXT
        return _ACTION_STALL

    def _op_clearevent(self, ops: dict[str, int], now: int) -> str:
        """Clear the masked event bits."""
        self.events &= ~ops["mask"]
        return _ACTION_NEXT

    def _op_clearmiss(self, ops: dict[str, int], now: int) -> str:
        """Clear the missed flag on each masked pin this engine owns.

        An engine never writes a cell it does not own, so mask bits naming
        unowned pins are ignored without faulting.
        """
        mask = ops["mask"]
        for index in range(PIN_COUNT):
            if (mask >> index) & 1 and (self.owner >> index) & 1:
                self.pins[index].clear_missed()
        return _ACTION_NEXT

    # -- CRC ------------------------------------------------------------

    def _op_crccfg(self, ops: dict[str, int], now: int) -> str:
        """Configure the shared CRC polynomial and direction; state untouched."""
        self.crc.configure(ops["poly"], bool(ops["msb_first"]))
        return _ACTION_NEXT

    def _op_crcload(self, ops: dict[str, int], now: int) -> str:
        """Seed the shared CRC with the low 16 bits of ``regs[reg]``."""
        self.crc.load(self.regs[ops["reg"]] & 0xFFFF)
        return _ACTION_NEXT

    def _op_crcbit(self, ops: dict[str, int], now: int) -> str:
        """Shift the low bit of ``regs[reg]`` through the shared CRC."""
        self.crc.shift(self.regs[ops["reg"]] & 1)
        return _ACTION_NEXT

    def _op_crcget(self, ops: dict[str, int], now: int) -> str:
        """``regs[reg]`` = the 16-bit shared CRC state, zero-extended."""
        self.regs[ops["reg"]] = self.crc.value & 0xFFFF
        return _ACTION_NEXT
