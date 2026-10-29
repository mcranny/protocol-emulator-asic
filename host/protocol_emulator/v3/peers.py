"""Independent wire-level protocol peers, used as test oracles.

These peers are written from the protocol specifications (UART 8N1 framing,
SPI modes 0-3, I2C START/STOP/ACK with its tLOW and tHIGH minimums, WS2812
T0H/T1H/reset), not from the firmware. They import nothing from the assembler,
the engine model or the firmware generators, so a firmware bug cannot be
mirrored into the oracle. Only :data:`protocol_emulator.v3.CLOCK_HZ` is shared,
because every timestamp is a tick of that clock.

**Samples.** A waveform is a list of ``(timestamp, state)`` pairs. The
timestamp is an integer tick of the 48 MHz clock. The state is the level of
the wire (single-wire peers: ``0`` or ``1``) or a :class:`SpiBus` /
:class:`I2cBus` (multi-wire peers). A sample is a *change point*: the state
holds from its timestamp until the next sample, and the final state holds until
the end of the observation. The first sample declares the initial state and is
never an edge. Timestamps must not decrease. When two samples share a
timestamp the later one wins. Repeating a state is allowed and has no effect.
The observation ends at the last sample's timestamp unless the decoder takes an
explicit ``end``.

**Glitches.** The chip qualifies a change on its *inputs* with three agreeing
samples, but its *outputs* have no such filter, so a real receiver sees every
pulse the chip drives. A pulse shorter than :data:`FILTER_SAMPLES` ticks on any
single wire is therefore always reported as a ``"glitch"`` violation, and by
default it stays in the waveform, so its effect on the decoded data and events
is visible too. Pass ``strip_glitches=True`` to a decoder to remove glitches
before decoding, which shows what a chip with its input filter would see; they
are still reported. The ``respond`` methods overlay MISO or SDA on the raw
samples and never remove a glitch.

**Idle levels.** Every decoder reports an ``"idle_level"`` violation if the
waveform does not start idle: UART high, WS2812 low, SPI with CS high and SCLK
at CPOL, I2C with SCL and SDA high.

**Results.** Decoders return a :class:`Result`. Violations are reported, never
silently accepted. Stimulus generators (:class:`UartTransmitter`,
:class:`SpiController`, :class:`I2cController`) return sample lists. Their
waveforms start with an idle sample at ``start`` and begin the first real edge
a short, documented interval later, so the idle level is never overwritten.
"""
from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterator, Sequence
from typing import NamedTuple

from . import CLOCK_HZ

#: A single-wire waveform: ``(timestamp, level)`` change points.
Samples = Sequence[tuple[int, int]]

#: Number of agreeing samples (clock ticks) needed to qualify a change. A pulse
#: shorter than this is a glitch. Source: the input filter row of the design
#: specification, "three agreeing samples qualify a change".
FILTER_SAMPLES = 3

#: Largest accepted relative error of a UART edge position, measured from the
#: start-bit edge. The design specification gives no UART tolerance. A receiver
#: sampling mid-bit survives up to half a bit of cumulative drift at the stop
#: bit's centre, 0.5 / 9.5 = 5.3% of the nominal rate, shared between
#: transmitter and receiver; common practice budgets about 2% to the
#: transmitter. This peer is deliberately stricter, at 1%, so that a 2% error
#: is flagged. The chip's own quantization (0.08% at 115200) is far inside it.
UART_TOLERANCE = 0.01

#: WS2812 nominal high times in nanoseconds (WS2812B datasheet): T0H 0.4 us
#: and T1H 0.8 us.
WS2812_T0H_NS = 400
WS2812_T1H_NS = 800
#: WS2812 nominal low times in nanoseconds: T0L 0.85 us and T1L 0.45 us.
WS2812_T0L_NS = 850
WS2812_T1L_NS = 450
#: WS2812 symmetric tolerance on every high and low time (datasheet +/-150 ns).
WS2812_TOLERANCE_NS = 150
#: WS2812 latch: the line held low at least this long is a reset. Source: the
#: design specification, "at least 300 us" (14,400 cycles at 48 MHz).
WS2812_RESET_NS = 300_000


def _ceil_cycles(seconds: float) -> int:
    """Whole clock cycles that cover ``seconds``, tolerant of float noise."""
    return math.ceil(seconds * CLOCK_HZ - 1e-9)


#: I2C minimum SCL ``(low, high)`` in clock cycles, keyed by bus rate. Source:
#: the design specification and the I2C-bus specification (UM10204): standard
#: mode tLOW 4.7 us, tHIGH 4.0 us; fast mode tLOW 1.3 us, tHIGH 0.6 us.
I2C_MINIMUM_CYCLES = {
    100_000: (_ceil_cycles(4.7e-6), _ceil_cycles(4.0e-6)),
    400_000: (_ceil_cycles(1.3e-6), _ceil_cycles(0.6e-6)),
}


def _ns_to_ticks(nanoseconds: int) -> float:
    """Convert nanoseconds to (fractional) clock ticks."""
    return nanoseconds * CLOCK_HZ / 1e9


class Violation(NamedTuple):
    """One protocol or timing violation found while decoding.

    :param timestamp: tick at which the violation was observed.
    :param kind: short machine-readable name, e.g. ``"glitch"`` or ``"tLOW"``.
    :param detail: human-readable explanation with the measured values.
    """

    timestamp: int
    kind: str
    detail: str


class Event(NamedTuple):
    """A decoded protocol event.

    :param timestamp: tick of the event.
    :param kind: event name; see each decoder for the kinds it emits.
    :param value: the byte for byte events, otherwise ``0``.
    """

    timestamp: int
    kind: str
    value: int = 0


class Result(NamedTuple):
    """Everything a decoder recovered from a waveform.

    :param data: decoded bytes: UART bytes received, SPI MOSI bytes, every I2C
        byte on the bus (address and data, either direction) or WS2812 bytes.
        Frames or bytes that violated the protocol framing are not included.
    :param violations: every violation found, in observation order.
    :param events: decoded events with timestamps.
    :param aux: secondary byte stream: SPI MISO bytes, empty for the others.
    """

    data: bytes
    violations: list[Violation]
    events: list[Event]
    aux: bytes = b""

    @property
    def ok(self) -> bool:
        """``True`` when no violation was reported."""
        return not self.violations


class SpiBus(NamedTuple):
    """Wire levels of an SPI bus at one instant.

    :param sclk: clock level.
    :param cs_n: active-low chip select; ``0`` means selected.
    :param mosi: controller-out, target-in data level.
    :param miso: controller-in, target-out data level.
    """

    sclk: int
    cs_n: int
    mosi: int
    miso: int


class I2cBus(NamedTuple):
    """Resolved wire levels of an I2C bus at one instant.

    The levels are what the wires carry after the wired-AND of every driver:
    a line is ``0`` if any device pulls it low, otherwise ``1`` via the
    pull-up.

    :param scl: SCL level.
    :param sda: SDA level.
    """

    scl: int
    sda: int


class I2cOp(NamedTuple):
    """One step of an :class:`I2cController` transaction.

    :param kind: ``"start"`` (START, or repeated START if a transaction is
        open), ``"write"``, ``"read"`` or ``"stop"``.
    :param value: for ``"write"`` the byte; for ``"read"`` ``1`` to ACK the
        byte received and ``0`` to NACK it; ignored otherwise.
    """

    kind: str
    value: int = 0


_FieldSamples = Sequence[tuple[int, Sequence[int]]]
_Cleaned = list[tuple[int, tuple[int, ...]]]


def _deglitch(
    changes: list[tuple[int, int]],
    name: str,
    violations: list[Violation],
    strip: bool,
) -> list[tuple[int, int]]:
    """Report pulses shorter than :data:`FILTER_SAMPLES` on one wire.

    ``changes`` alternates level at every entry. The first entry (the initial
    state) and the last (held to the end) are never glitches. With ``strip``
    each glitch is also removed (merging its neighbours); otherwise the
    waveform is returned unchanged.
    """
    out = list(changes)
    index = 1
    while index < len(out) - 1:
        width = out[index + 1][0] - out[index][0]
        if width < FILTER_SAMPLES:
            violations.append(
                Violation(
                    out[index][0],
                    "glitch",
                    f"{name} pulse of {width} ticks is under the "
                    f"{FILTER_SAMPLES}-sample filter window",
                )
            )
            if strip:
                del out[index : index + 2]
                continue
        index += 1
    return out


def _clean(
    samples: _FieldSamples, names: Sequence[str], strip_glitches: bool = False
) -> tuple[_Cleaned, list[Violation], int]:
    """Validate and collapse a waveform, reporting (optionally removing) glitches.

    :returns: the cleaned change list, the glitch violations and the
        timestamp of the last input sample.
    :raises ValueError: for empty input, a wrong state width, a non-binary
        level or decreasing timestamps.
    """
    if not samples:
        raise ValueError("waveform has no samples")
    previous = samples[0][0]
    for timestamp, state in samples:
        if len(state) != len(names):
            raise ValueError(f"state must have {len(names)} levels")
        if any(level not in (0, 1) for level in state):
            raise ValueError("levels must be 0 or 1")
        if timestamp < previous:
            raise ValueError("timestamps must not decrease")
        previous = timestamp
    violations: list[Violation] = []
    per_wire: list[list[tuple[int, int]]] = []
    for index, name in enumerate(names):
        changes: list[tuple[int, int]] = []
        for timestamp, state in samples:
            level = state[index]
            if changes and changes[-1][0] == timestamp:
                changes.pop()
            if changes and changes[-1][1] == level:
                continue
            changes.append((timestamp, level))
        per_wire.append(_deglitch(changes, name, violations, strip_glitches))
    lookup = [dict(changes) for changes in per_wire]
    times = sorted({timestamp for changes in per_wire for timestamp, _ in changes})
    levels = [changes[0][1] for changes in per_wire]
    cleaned: _Cleaned = []
    for timestamp in times:
        levels = [table.get(timestamp, level) for table, level in zip(lookup, levels)]
        cleaned.append((timestamp, tuple(levels)))
    violations.sort(key=lambda violation: violation.timestamp)
    return cleaned, violations, samples[-1][0]


def _single(samples: Samples) -> _FieldSamples:
    """Wrap single-wire samples as one-level states."""
    return [(timestamp, (level,)) for timestamp, level in samples]


class UartTransmitter:
    """Generate an 8N1 UART waveform for the chip to receive.

    Framing per the UART convention: line idles high; each frame is one low
    start bit, eight data bits least significant first, one high stop bit.

    :param bit_cycles: clock ticks per bit.
    """

    def __init__(self, bit_cycles: int) -> None:
        if bit_cycles < 1:
            raise ValueError("bit_cycles must be positive")
        self.bit_cycles = bit_cycles

    def encode(self, data: bytes, start: int = 0, gap_bits: int = 0) -> list[tuple[int, int]]:
        """Return the waveform that sends ``data``.

        The waveform opens with an idle-high sample at ``start``. The first
        start bit begins one bit period later. A final idle-high sample marks
        the end of the last stop bit.

        :param data: bytes to send, in order.
        :param start: tick of the opening idle sample.
        :param gap_bits: extra idle bit periods inserted between frames; ``0``
            sends frames back to back.
        """
        out = [(start, 1)]
        now = start + self.bit_cycles
        for position, value in enumerate(data):
            if position:
                now += gap_bits * self.bit_cycles
            frame = [0] + [(value >> bit) & 1 for bit in range(8)] + [1]
            for level in frame:
                out.append((now, level))
                now += self.bit_cycles
        out.append((now, 1))
        return out


class UartReceiver:
    """Decode 8N1 UART frames from a single-wire waveform.

    The receiver finds a falling edge while idle, confirms the start bit is
    still low at its centre, then samples the eight data bits and the stop bit
    at their centres, taken from the start edge at the nominal bit period.
    Reported violations:

    * ``"glitch"``: a pulse under the filter window (kept unless
      ``strip_glitches``).
    * ``"idle_level"``: the line starts low.
    * ``"false_start"``: a falling edge whose start bit is high again by its
      centre.
    * ``"stop_short"``: after an accepted frame the next start edge comes
      sooner than ``10 * bit_cycles * (1 - tolerance)`` after the previous
      start edge, so the stop bit was too short. The next frame is still
      decoded.
    * ``"framing"``: the stop bit is low. The byte is left out of
      :attr:`Result.data` (an ``"framing_error"`` event carries it) and the
      receiver waits for the line to return high before looking for a new
      frame.
    * ``"bit_period"``: an edge inside the frame lands more than ``tolerance``
      (relative, measured from the start edge) from the nearest whole bit.
      The byte is still decoded.
    * ``"truncated"``: the observation ends before the stop bit's centre.

    Emitted events: ``"byte"`` and ``"framing_error"``, with the byte as value.

    :param bit_cycles: nominal clock ticks per bit.
    :param tolerance: edge-position tolerance, default :data:`UART_TOLERANCE`.
    """

    def __init__(self, bit_cycles: int, tolerance: float = UART_TOLERANCE) -> None:
        if bit_cycles < 1:
            raise ValueError("bit_cycles must be positive")
        self.bit_cycles = bit_cycles
        self.tolerance = tolerance

    def decode(
        self, samples: Samples, end: int | None = None, strip_glitches: bool = False
    ) -> Result:
        """Decode ``samples``.

        :param samples: ``(timestamp, level)`` change points.
        :param end: tick the observation ends. By default the last level is
            taken to hold for one more bit period after the last sample, so a
            trace that ends on its final stop edge still contains that stop
            bit's centre. A frame cut short by more than that is reported as
            ``"truncated"``.
        :param strip_glitches: remove glitches before decoding (they are still
            reported).
        """
        cleaned, violations, last = _clean(_single(samples), ("line",), strip_glitches)
        stop_time = last + self.bit_cycles if end is None else end
        times = [timestamp for timestamp, _ in cleaned]
        levels = [state[0] for _, state in cleaned]
        period = self.bit_cycles
        data = bytearray()
        events: list[Event] = []
        if levels[0] == 0:
            violations.append(Violation(times[0], "idle_level", "line starts low, idle is high"))

        def level_at(moment: float) -> int:
            return levels[max(bisect_right(times, moment) - 1, 0)]

        resume = 0.0
        earliest_start = 0.0
        for index in range(1, len(times)):
            edge = times[index]
            if levels[index] != 0 or edge < resume:
                continue
            if level_at(edge + period / 2):
                violations.append(
                    Violation(edge, "false_start", "start bit is high again by its centre")
                )
                continue
            if edge < earliest_start:
                violations.append(
                    Violation(
                        edge,
                        "stop_short",
                        f"start edge {earliest_start - edge:.1f} ticks before the "
                        "end of the minimum stop bit",
                    )
                )
            stop_centre = edge + 9.5 * period
            if stop_centre > stop_time:
                violations.append(
                    Violation(stop_time, "truncated", "observation ends inside a frame")
                )
                break
            value = sum(level_at(edge + (bit + 1.5) * period) << bit for bit in range(8))
            first = index + 1
            stop = bisect_left(times, stop_centre)
            for position in range(first, stop):
                offset = times[position] - edge
                bits = max(round(offset / period), 1)
                error = (offset - bits * period) / (bits * period)
                if abs(error) > self.tolerance:
                    violations.append(
                        Violation(
                            times[position],
                            "bit_period",
                            f"edge is {error:+.2%} from bit boundary {bits} "
                            f"(tolerance {self.tolerance:.2%})",
                        )
                    )
                    break
            centre_tick = edge + round(9.5 * period)
            if level_at(stop_centre):
                data.append(value)
                events.append(Event(centre_tick, "byte", value))
                resume = stop_centre
                earliest_start = edge + 10 * period * (1 - self.tolerance)
            else:
                earliest_start = 0.0
                violations.append(Violation(centre_tick, "framing", "stop bit is low"))
                events.append(Event(centre_tick, "framing_error", value))
                after = bisect_right(times, stop_centre)
                if after >= len(times):
                    break
                resume = float(times[after])
        violations.sort(key=lambda violation: violation.timestamp)
        return Result(bytes(data), violations, events)


def _spi_mode(mode: int) -> tuple[int, int]:
    """Return ``(cpol, cpha)`` for SPI ``mode`` 0-3."""
    if mode not in (0, 1, 2, 3):
        raise ValueError("SPI mode must be 0-3")
    return mode >> 1, mode & 1


_SPI_FIELDS = ("sclk", "cs_n", "mosi", "miso")


def _spi_walk(
    cleaned: _Cleaned, cpol: int, cpha: int, violations: list[Violation]
) -> Iterator[tuple[int, str, SpiBus, SpiBus]]:
    """Classify every sample of a cleaned SPI waveform.

    Yields ``(timestamp, kind, previous, current)``. ``kind`` is ``"assert"``
    (chip select falls, or is already low at the start), ``"release"``
    (chip select rises), ``"sample"`` (the CPHA sampling edge while selected),
    ``"shift"`` (the other edge while selected) or ``"other"``.
    """
    previous = SpiBus(*cleaned[0][1])
    for position, (timestamp, state) in enumerate(cleaned):
        current = SpiBus(*state)
        kind = "other"
        if position == 0:
            if current.cs_n == 0:
                kind = "assert"
                violations.append(
                    Violation(timestamp, "idle_level", "CS is asserted at the start")
                )
            if current.sclk != cpol:
                violations.append(
                    Violation(
                        timestamp,
                        "idle_level",
                        f"SCLK is {current.sclk} at the start, CPOL is {cpol}",
                    )
                )
        elif current.cs_n != previous.cs_n:
            if current.sclk != previous.sclk:
                violations.append(
                    Violation(timestamp, "cs_sclk_same_tick", "CS and SCLK changed on one tick")
                )
            if current.cs_n == 0:
                kind = "assert"
                if current.sclk != cpol:
                    violations.append(
                        Violation(
                            timestamp,
                            "clock_idle",
                            f"SCLK is {current.sclk} at CS assert, CPOL is {cpol}",
                        )
                    )
            else:
                kind = "release"
                if current.sclk != cpol:
                    violations.append(
                        Violation(
                            timestamp,
                            "clock_idle",
                            f"SCLK is {current.sclk} at CS release, CPOL is {cpol}",
                        )
                    )
        elif current.cs_n == 0 and current.sclk != previous.sclk:
            leading = current.sclk != cpol
            if leading == (cpha == 0):
                kind = "sample"
                if current.mosi != previous.mosi or current.miso != previous.miso:
                    violations.append(
                        Violation(
                            timestamp,
                            "data_at_sample_edge",
                            "a data line changed on the sampling edge",
                        )
                    )
            else:
                kind = "shift"
        yield timestamp, kind, previous, current
        previous = current


class _SpiDecoder:
    """Shared SPI decoding for :class:`SpiTarget` and :class:`SpiController`."""

    def __init__(self, mode: int) -> None:
        self.mode = mode
        self.cpol, self.cpha = _spi_mode(mode)

    def decode(
        self, samples: Sequence[tuple[int, SpiBus]], strip_glitches: bool = False
    ) -> Result:
        """Decode eight-bit transfers, MSB first, from bus samples.

        Bits are taken at the CPHA sampling edge while chip select is low.
        :attr:`Result.data` holds the MOSI bytes and :attr:`Result.aux` the MISO
        bytes. Several bytes may share one chip-select assertion. Reported
        violations: ``"glitch"`` (kept in the waveform unless
        ``strip_glitches``); ``"idle_level"`` (CS low, or SCLK not at CPOL, at
        the start); ``"clock_idle"`` (SCLK not at CPOL when CS asserts or
        releases); ``"cs_sclk_same_tick"``; ``"data_at_sample_edge"`` (a data
        line changes on the very tick of its sampling edge); ``"cs_mid_byte"``
        (chip select released with a partial byte, which is dropped);
        ``"truncated"`` (observation ends with a partial byte). Events:
        ``"cs_assert"``, ``"cs_release"``, ``"mosi"`` and ``"miso"`` (value is
        the byte).

        Setup and hold are only checked as a same-tick change: the
        specification gives no setup or hold time for SPI, so a data change
        one tick before or after the sampling edge is accepted.
        """
        cleaned, violations, last = _clean(samples, _SPI_FIELDS, strip_glitches)
        mosi = bytearray()
        miso = bytearray()
        events: list[Event] = []
        out_bits: list[int] = []
        in_bits: list[int] = []
        for timestamp, kind, previous, _current in _spi_walk(
            cleaned, self.cpol, self.cpha, violations
        ):
            if kind == "assert":
                out_bits, in_bits = [], []
                events.append(Event(timestamp, "cs_assert"))
            elif kind == "sample":
                out_bits.append(previous.mosi)
                in_bits.append(previous.miso)
                if len(out_bits) == 8:
                    out_value = int("".join(map(str, out_bits)), 2)
                    in_value = int("".join(map(str, in_bits)), 2)
                    mosi.append(out_value)
                    miso.append(in_value)
                    events.append(Event(timestamp, "mosi", out_value))
                    events.append(Event(timestamp, "miso", in_value))
                    out_bits, in_bits = [], []
            elif kind == "release":
                if out_bits:
                    violations.append(
                        Violation(
                            timestamp,
                            "cs_mid_byte",
                            f"CS released after {len(out_bits)} of 8 bits",
                        )
                    )
                out_bits, in_bits = [], []
                events.append(Event(timestamp, "cs_release"))
        if out_bits:
            violations.append(
                Violation(last, "truncated", f"observation ends after {len(out_bits)} of 8 bits")
            )
        violations.sort(key=lambda violation: violation.timestamp)
        return Result(bytes(mosi), violations, events, bytes(miso))


class SpiTarget(_SpiDecoder):
    """SPI target peer: decodes a controller's transfers, optionally answers.

    Use it to check the chip acting as an SPI controller. :meth:`decode`
    recovers the MOSI bytes (and the MISO bytes if MISO is driven);
    :meth:`respond` supplies the MISO waveform for a canned reply.

    :param mode: SPI mode 0-3 (CPOL = ``mode >> 1``, CPHA = ``mode & 1``).
    """

    def respond(
        self, samples: Sequence[tuple[int, SpiBus]], reply: bytes
    ) -> list[tuple[int, SpiBus]]:
        """Return the bus with MISO driven so the target answers ``reply``.

        ``reply`` is shifted out MSB first, restarting at each chip-select
        assertion, and padded with zero bits when exhausted. The first bit is
        presented at chip-select assert in CPHA 0 and on the first leading
        edge in CPHA 1; later bits change on the shifting edge. MISO is ``0``
        while the target is not selected. Any MISO in ``samples`` is replaced.

        The overlay is post hoc: the controller waveform is recorded first and
        the target's MISO is computed from its clock edges afterwards. That is
        faithful only because the controller's timing does not depend on what
        the target answers, which holds for firmware whose timing is
        data-independent. A controller that reacts to MISO (arbitration,
        clock stretching, early exit) is not modelled.

        The result has one sample per input sample, with SCLK, CS and MOSI
        exactly as given: glitches are neither removed nor hidden, so decoding
        the result still reports them. Violations found while walking the
        waveform are not returned here; call :meth:`decode` on the result.
        """
        cleaned, _violations, _last = _clean(samples, _SPI_FIELDS)
        bits = [(byte >> (7 - bit)) & 1 for byte in reply for bit in range(8)]

        def bit_at(index: int) -> int:
            return bits[index] if index < len(bits) else 0

        walked: list[tuple[int, int]] = []
        miso = 0
        shifted = 0
        for timestamp, kind, _previous, _current in _spi_walk(
            cleaned, self.cpol, self.cpha, []
        ):
            if kind == "assert":
                shifted = 0
                miso = bit_at(0) if self.cpha == 0 else 0
            elif kind == "shift":
                if self.cpha == 0:
                    shifted += 1
                    miso = bit_at(shifted)
                else:
                    miso = bit_at(shifted)
                    shifted += 1
            elif kind == "release":
                miso = 0
            walked.append((timestamp, miso))
        walked_times = [timestamp for timestamp, _ in walked]
        return [
            (
                timestamp,
                SpiBus(
                    state.sclk,
                    state.cs_n,
                    state.mosi,
                    walked[bisect_right(walked_times, timestamp) - 1][1],
                ),
            )
            for timestamp, state in samples
        ]


class SpiController(_SpiDecoder):
    """SPI controller peer: generates stimulus for the chip acting as a target.

    The generated waveform drives SCLK, CS and MOSI; MISO is ``0``. To read
    back what the chip drives on MISO, merge the chip's MISO into the samples
    and call :meth:`decode` (the bytes are in :attr:`Result.aux`).

    :param mode: SPI mode 0-3.
    :param half_cycles: clock ticks per SCLK half period.
    """

    def __init__(self, mode: int, half_cycles: int) -> None:
        super().__init__(mode)
        if half_cycles < 1:
            raise ValueError("half_cycles must be positive")
        self.half_cycles = half_cycles

    def transfer(self, data: bytes, start: int = 0) -> list[tuple[int, SpiBus]]:
        """Return one chip-select assertion that clocks out ``data``.

        The waveform opens with the idle bus at ``start`` (CS high, SCLK at
        CPOL). CS falls one half period later. In CPHA 0 the first bit is on
        MOSI from CS assert and later bits change on trailing edges; in CPHA 1
        bits change on leading edges. The first leading edge comes one half
        period after CS falls, and CS rises one half period after the last
        trailing edge. A bit is therefore stable for a full half period
        before its sampling edge.
        """
        half = self.half_cycles
        cpol, cpha = self.cpol, self.cpha
        bits = [(byte >> (7 - bit)) & 1 for byte in data for bit in range(8)]
        out = [(start, SpiBus(cpol, 1, 0, 0))]
        now = start + half
        mosi = bits[0] if bits and cpha == 0 else 0
        out.append((now, SpiBus(cpol, 0, mosi, 0)))
        for index, bit in enumerate(bits):
            lead = now + half
            trail = lead + half
            if cpha == 1:
                mosi = bit
            out.append((lead, SpiBus(cpol ^ 1, 0, mosi, 0)))
            if cpha == 0 and index + 1 < len(bits):
                mosi = bits[index + 1]
            out.append((trail, SpiBus(cpol, 0, mosi, 0)))
            now = trail
        out.append((now + half, SpiBus(cpol, 1, mosi, 0)))
        return out


#: Clock pulses outside a transaction that make up a byte pattern (eight data
#: clocks) and so are reported unless a bus clear is allowed.
_I2C_STRAY_CLOCKS = 8


def _i2c_scan(
    cleaned: _Cleaned,
    min_low: int,
    min_high: int,
    violations: list[Violation],
    allow_bus_clear: bool = False,
) -> tuple[list[Event], bytearray, list[tuple[int, int | None]], int]:
    """Decode a cleaned I2C waveform.

    :returns: ``(events, data, ack_windows, last)`` where each ack window is
        ``(open, close)``: the SCL fall that ends bit 8 and the SCL fall that
        ends the ACK clock (``None`` if the observation ends first).
    """
    events: list[Event] = []
    data = bytearray()
    windows: list[tuple[int, int | None]] = []
    in_frame = False
    bits: list[int] = []
    fall_time: int | None = None
    rise_time: int | None = None
    pulse_is_clock = True
    pending: tuple[int, int] | None = None
    window_open: int | None = None
    stray_clocks = 0
    previous_scl, previous_sda = cleaned[0][1]
    if (previous_scl, previous_sda) != (1, 1):
        violations.append(
            Violation(
                cleaned[0][0],
                "idle_level",
                f"SCL={previous_scl} SDA={previous_sda} at the start, idle is both high",
            )
        )
    for timestamp, (scl, sda) in cleaned[1:]:
        if scl != previous_scl and sda != previous_sda:
            violations.append(
                Violation(timestamp, "simultaneous_edges", "SCL and SDA changed on one tick")
            )
        if scl != previous_scl:
            if scl:
                if fall_time is not None and timestamp - fall_time < min_low:
                    violations.append(
                        Violation(
                            timestamp,
                            "tLOW",
                            f"SCL low {timestamp - fall_time} ticks, minimum {min_low}",
                        )
                    )
                rise_time = timestamp
                pulse_is_clock = True
                pending = (timestamp, previous_sda)
            else:
                if rise_time is not None and pulse_is_clock and timestamp - rise_time < min_high:
                    violations.append(
                        Violation(
                            timestamp,
                            "tHIGH",
                            f"SCL high {timestamp - rise_time} ticks, minimum {min_high}",
                        )
                    )
                fall_time = timestamp
                if in_frame and pending is not None:
                    bits.append(pending[1])
                    if len(bits) == 9:
                        value = int("".join(map(str, bits[:8])), 2)
                        data.append(value)
                        events.append(Event(pending[0], "byte", value))
                        events.append(Event(pending[0], "nack" if bits[8] else "ack", value))
                        bits = []
                        if window_open is not None:
                            windows.append((window_open, timestamp))
                            window_open = None
                    elif len(bits) == 8:
                        window_open = timestamp
                elif not in_frame and pending is not None and pulse_is_clock:
                    stray_clocks += 1
                    if stray_clocks == _I2C_STRAY_CLOCKS and not allow_bus_clear:
                        violations.append(
                            Violation(
                                timestamp,
                                "clock_outside_frame",
                                f"{stray_clocks} SCL pulses with no START, "
                                "a byte pattern outside a transaction",
                            )
                        )
                pending = None
        if sda != previous_sda and scl:
            pulse_is_clock = False
            pending = None
            stray_clocks = 0
            if not sda:
                if in_frame:
                    if bits:
                        violations.append(
                            Violation(
                                timestamp,
                                "partial_byte",
                                f"repeated START after {len(bits)} of 9 bits",
                            )
                        )
                    events.append(Event(timestamp, "restart"))
                else:
                    events.append(Event(timestamp, "start"))
                in_frame = True
            else:
                if in_frame and bits:
                    violations.append(
                        Violation(
                            timestamp, "partial_byte", f"STOP after {len(bits)} of 9 bits"
                        )
                    )
                events.append(Event(timestamp, "stop"))
                in_frame = False
            bits = []
            window_open = None
        previous_scl, previous_sda = scl, sda
    last = cleaned[-1][0]
    if in_frame and bits:
        violations.append(
            Violation(last, "truncated", f"observation ends after {len(bits)} of 9 bits")
        )
    if in_frame:
        violations.append(
            Violation(last, "unterminated", "transaction still open: no STOP before the end")
        )
    if window_open is not None:
        windows.append((window_open, None))
    return events, data, windows, last


class I2cTarget:
    """I2C target peer: decodes the bus, and can drive ACK.

    Use it to check the chip acting as an I2C controller, or to decode the bus
    in any direction: decoding is direction-agnostic and records each nine-bit
    group as a byte plus its ACK or NACK bit.

    :meth:`decode` reports ``"glitch"`` (kept in the waveform unless
    ``strip_glitches``), ``"idle_level"`` (SCL or SDA low at the start),
    ``"tLOW"`` and ``"tHIGH"`` (SCL low
    or high time under the minimum for the bus rate, checked on every clock
    pulse that does not contain a START or STOP), ``"partial_byte"`` (START or
    STOP before the ninth bit), ``"truncated"``, ``"unterminated"`` (a
    transaction is still open, with no STOP, when the observation ends),
    ``"clock_outside_frame"`` (eight or more SCL pulses with no START, which
    is a byte pattern with no transaction) and ``"simultaneous_edges"``
    (SCL and SDA change on one tick, which leaves no data setup or hold).
    Events: ``"start"``, ``"restart"`` (repeated START), ``"byte"``, ``"ack"``
    or ``"nack"`` (value is the byte), ``"stop"``. Clock pulses seen while no
    transaction is open are timed but not decoded. A bit is sampled on the SCL rise and counted when SCL falls; a pulse that
    contains a START or STOP is the setup for that condition, not a data
    clock.

    The START and STOP setup and hold times, tSU;DAT and tBUF are not checked.

    :param rate: bus rate in Hz, 100000 or 400000.
    """

    def __init__(self, rate: int = 100_000) -> None:
        if rate not in I2C_MINIMUM_CYCLES:
            raise ValueError(f"unsupported I2C rate {rate}")
        self.rate = rate
        self.min_low, self.min_high = I2C_MINIMUM_CYCLES[rate]

    def decode(
        self,
        samples: Sequence[tuple[int, I2cBus]],
        strip_glitches: bool = False,
        allow_bus_clear: bool = False,
    ) -> Result:
        """Decode ``(timestamp, I2cBus)`` samples; see the class description.

        :param strip_glitches: remove glitches before decoding (they are still
            reported).
        :param allow_bus_clear: accept a run of SCL pulses outside a
            transaction (the nine-clock bus-clear sequence) instead of
            reporting ``"clock_outside_frame"``.
        """
        cleaned, violations, _last = _clean(samples, ("scl", "sda"), strip_glitches)
        events, data, _windows, _end = _i2c_scan(
            cleaned, self.min_low, self.min_high, violations, allow_bus_clear
        )
        violations.sort(key=lambda violation: violation.timestamp)
        return Result(bytes(data), violations, events)

    def respond(
        self, samples: Sequence[tuple[int, I2cBus]], ack: Sequence[bool] = ()
    ) -> list[tuple[int, I2cBus]]:
        """Return the resolved bus with this target ACKing a write transaction.

        SDA is pulled low one tick after the SCL fall that ends the eighth
        bit (so SCL and SDA never change on one tick) and released one tick
        after the SCL fall that ends the ninth clock, for each byte whose
        ``ack`` entry is true. Byte ``n`` (counted across the whole waveform,
        address included) is ACKed unless ``ack[n]`` exists and is false, in
        which case SDA is left to the controller (NACK).

        The overlay is post hoc: the controller waveform is recorded first and
        the target's SDA is computed from its clock edges afterwards. That is
        faithful only because the controller's timing does not depend on what
        the target does, which holds for firmware whose timing is
        data-independent. Clock stretching, arbitration and a target-driven
        data phase (reads) are not modelled; the data bits of a read are
        whatever the controller left on SDA.

        The result contains every input sample with SCL and SDA exactly as
        given, plus the ACK pulses. Glitches are neither removed nor hidden,
        so decoding the result still reports them.
        """
        cleaned, violations, _last = _clean(samples, ("scl", "sda"))
        _events, _data, windows, _end = _i2c_scan(cleaned, 0, 0, violations, True)
        drive: list[tuple[int, int]] = [(cleaned[0][0], 1)]
        for index, (opened, closed) in enumerate(windows):
            if index < len(ack) and not ack[index]:
                continue
            drive.append((opened + 1, 0))
            if closed is not None:
                drive.append((closed + 1, 1))
        controller_times = [timestamp for timestamp, _ in samples]
        drive_times = [timestamp for timestamp, _ in drive]
        out: list[tuple[int, I2cBus]] = []
        for timestamp in sorted(set(controller_times) | set(drive_times)):
            scl, sda = samples[bisect_right(controller_times, timestamp) - 1][1]
            pulled = drive[bisect_right(drive_times, timestamp) - 1][1]
            out.append((timestamp, I2cBus(scl, sda & pulled)))
        return out


class I2cController:
    """I2C controller peer: generates stimulus for the chip acting as a target.

    The waveform is what the controller drives, as resolved bus levels with
    the target absent: SDA is released (high) in every ACK slot and during
    every ``"read"`` data phase. Use :meth:`I2cTarget.respond` to add a
    target's ACK pulses, or merge the chip's own SDA by hand.

    The controller's own timing is ideal: SCL is low for ``low_cycles`` and
    high for ``high_cycles`` on every clock, SDA changes in the middle of the
    low phase, and START, repeated START and STOP hold SCL high for a full
    low or high period around the SDA edge.

    :param low_cycles: SCL low time in clock ticks.
    :param high_cycles: SCL high time in clock ticks.
    """

    def __init__(self, low_cycles: int, high_cycles: int) -> None:
        if low_cycles < 2 or high_cycles < 1:
            raise ValueError("clock phases are too short")
        self.low_cycles = low_cycles
        self.high_cycles = high_cycles

    def transaction(self, ops: Sequence[I2cOp], start: int = 0) -> list[tuple[int, I2cBus]]:
        """Return the waveform for ``ops``.

        The waveform opens with the idle bus at ``start``; the first START
        follows one SCL low time later. It ends with a repeated sample of the
        final state one SCL low time after the last edge (bus-free time after a
        STOP).

        :raises ValueError: for an unknown op, or a write, read or stop before
            any START.
        """
        low, high = self.low_cycles, self.high_cycles
        out = [(start, I2cBus(1, 1))]
        now = start
        scl = sda = 1
        open_ = False

        def put(new_scl: int | None = None, new_sda: int | None = None) -> None:
            nonlocal scl, sda
            if new_scl is not None:
                scl = new_scl
            if new_sda is not None:
                sda = new_sda
            out.append((now, I2cBus(scl, sda)))

        def bit(level: int) -> None:
            nonlocal now
            now += low // 2
            put(new_sda=level)
            now += low - low // 2
            put(new_scl=1)
            now += high
            put(new_scl=0)

        for op in ops:
            if op.kind == "start":
                if scl == 0:
                    now += low // 2
                    put(new_sda=1)
                    now += low - low // 2
                    put(new_scl=1)
                now += low
                put(new_sda=0)
                now += high
                put(new_scl=0)
                open_ = True
            elif op.kind in ("write", "read", "stop"):
                if not open_:
                    raise ValueError(f"{op.kind} before START")
                if op.kind == "write":
                    for position in range(8):
                        bit((op.value >> (7 - position)) & 1)
                    bit(1)
                elif op.kind == "read":
                    for _ in range(8):
                        bit(1)
                    bit(0 if op.value else 1)
                else:
                    now += low // 2
                    put(new_sda=0)
                    now += low - low // 2
                    put(new_scl=1)
                    now += high
                    put(new_sda=1)
                    open_ = False
            else:
                raise ValueError(f"unknown I2C op {op.kind!r}")
        now += low
        put()
        return out


class Ws2812Receiver:
    """Decode WS2812 symbols from a single-wire waveform.

    A symbol is a high pulse followed by a low. The bit is the nominal high
    time nearest the measured one (T0H 0.4 us or T1H 0.8 us). Every high and
    every following low (when another bit follows) must be within
    :data:`WS2812_TOLERANCE_NS` of nominal (T0L 0.85 us, T1L 0.45 us). A low of
    at least :data:`WS2812_RESET_NS` is a reset, which latches the data and
    ends the byte alignment. Bits are packed eight to a byte, first bit most
    significant. The colour order (GRB) is left to the caller.

    Reported violations: ``"glitch"`` (kept in the waveform unless
    ``strip_glitches``), ``"idle_level"`` (the line starts high; idle is low,
    and since the start of that first high pulse is unknown it is not
    decoded), ``"high_time"`` and ``"low_time"``
    (outside the tolerance window, the bit is still decoded by nearest high
    time), ``"partial_byte"`` (a reset or the end of the observation arrives
    between byte boundaries), ``"truncated"`` (the line ends high, so the
    last high time is unknown) and, with ``require_reset``, ``"no_reset"``.
    A low shorter than the reset threshold at
    the very end of the observation cannot be judged and is not reported.
    Events: ``"byte"`` (at the byte's last falling edge) and ``"reset"``
    (when the low has lasted the reset time).
    """

    def decode(
        self,
        samples: Samples,
        end: int | None = None,
        strip_glitches: bool = False,
        require_reset: bool = False,
    ) -> Result:
        """Decode ``samples``.

        :param samples: ``(timestamp, level)`` change points.
        :param end: tick the observation ends; default the last timestamp.
            A reset is only recognized if the low is known to last that long,
            so end the waveform after the gap, or pass ``end``. No period is
            assumed to follow the last sample: the length of the final low is
            only known up to ``end``.
        :param strip_glitches: remove glitches before decoding (they are still
            reported).
        :param require_reset: report ``"no_reset"`` if the observation ends
            with symbols that no reset low has latched.
        """
        cleaned, violations, last = _clean(_single(samples), ("line",), strip_glitches)
        stop_time = last if end is None else end
        times = [timestamp for timestamp, _ in cleaned]
        levels = [state[0] for _, state in cleaned]
        tolerance = _ns_to_ticks(WS2812_TOLERANCE_NS)
        t0h = _ns_to_ticks(WS2812_T0H_NS)
        t1h = _ns_to_ticks(WS2812_T1H_NS)
        t0l = _ns_to_ticks(WS2812_T0L_NS)
        t1l = _ns_to_ticks(WS2812_T1L_NS)
        reset_ticks = -(-WS2812_RESET_NS * CLOCK_HZ // 10**9)
        data = bytearray()
        events: list[Event] = []
        bits: list[int] = []
        unlatched = 0
        if levels[0] == 1:
            violations.append(Violation(times[0], "idle_level", "line starts high, idle is low"))
        for index, level in enumerate(levels):
            if level != 1 or index == 0:
                continue
            rise = times[index]
            if index + 1 >= len(times):
                violations.append(
                    Violation(rise, "truncated", "line ends high; last high time unknown")
                )
                break
            fall = times[index + 1]
            high = fall - rise
            symbol = 1 if high > (t0h + t1h) / 2 else 0
            if abs(high - (t1h if symbol else t0h)) > tolerance + 1e-9:
                violations.append(
                    Violation(
                        rise,
                        "high_time",
                        f"high {high} ticks for a {symbol} bit, nominal "
                        f"{(t1h if symbol else t0h):.1f} +/- {tolerance:.1f}",
                    )
                )
            bits.append(symbol)
            unlatched += 1
            if len(bits) == 8:
                value = int("".join(map(str, bits)), 2)
                data.append(value)
                events.append(Event(fall, "byte", value))
                bits = []
            has_next = index + 2 < len(times)
            low = (times[index + 2] if has_next else stop_time) - fall
            if low >= reset_ticks:
                if bits:
                    violations.append(
                        Violation(
                            fall, "partial_byte", f"reset after {len(bits)} bits of a byte"
                        )
                    )
                    bits = []
                unlatched = 0
                events.append(Event(fall + reset_ticks, "reset"))
            elif has_next and abs(low - (t1l if symbol else t0l)) > tolerance + 1e-9:
                violations.append(
                    Violation(
                        fall,
                        "low_time",
                        f"low {low} ticks after a {symbol} bit, nominal "
                        f"{(t1l if symbol else t0l):.1f} +/- {tolerance:.1f}",
                    )
                )
        if bits:
            violations.append(
                Violation(stop_time, "partial_byte", f"observation ends after {len(bits)} bits")
            )
        if require_reset and unlatched:
            violations.append(
                Violation(
                    stop_time,
                    "no_reset",
                    f"{unlatched} symbols at the end are not followed by a reset low",
                )
            )
        violations.sort(key=lambda violation: violation.timestamp)
        return Result(bytes(data), violations, events)
