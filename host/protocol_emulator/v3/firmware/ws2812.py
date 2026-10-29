"""WS2812 transmit firmware for the V3 engines.

The WS2812 is an addressable RGB LED with a single-wire, self-clocked
protocol: every bit is a fixed-period high pulse followed by a low, and the
bit value is carried entirely by the width of the high. A long low latches the
pixels. The chip has no WS2812 peripheral and was never designed for it; this
module drives one end to end in firmware, with no change to the silicon. It is
the clearest flexibility demonstration in the project.

Symbol timing comes only from :data:`protocol_emulator.v3.timing.WS2812`, which
gives the ``(high, low)`` cycle counts for a ``"0"`` and a ``"1"`` symbol, and
from :data:`protocol_emulator.v3.timing.WS2812_RESET`, the latch low. No cycle
count is written as a literal here.

Waveform
--------

Each bit is one ``OUT`` scheduling the rising edge at the cursor, an ``ADVANCE``
by the symbol's high time, one ``OUT`` scheduling the falling edge, and an
``ADVANCE`` by the symbol's low time, so one period of the cursor passes per
bit. The pin cell's single pending slot throttles the loop exactly as it does
for UART: the ``OUT`` scheduling a falling edge waits until the rising edge it
follows has fired, and the next bit's rising ``OUT`` waits until that falling
edge has fired. The loop therefore paces itself to the wire with no explicit
synchronisation inside a bit.

The bit value selects the high and low times, so each bit branches. As with
UART the two branches are not the same length, and as with UART that cannot
jitter the line: every edge is scheduled at the cursor, both branches advance
the cursor by exactly one period, and the edge positions depend only on the
cursor, never on how many instructions a branch took to reach it.

The data bit is tested by shifting the byte left one place and branching on the
carry, so the most significant bit is sent first with no mask register. The
byte is left-justified into the top of the register first (``SHL`` by 16) so
that bit 7 lands on bit 23, the bit the shifter's carry reports.

Strip length
------------

The host frames a strip with a leading **count byte**: the first transmit-queue
entry of a frame is the number of colour bytes that follow, and the program
sends exactly that many bytes as a continuous, gap-free bit stream before it
holds the line low for :data:`protocol_emulator.v3.timing.WS2812_RESET` to latch
the pixels. A frame of *N* LEDs is therefore ``3 * N`` colour bytes preceded by
the byte ``3 * N`` (``bytes([9, g0, r0, b0, g1, r1, b1, g2, r2, b2])`` for three
LEDs). A count of zero is a no-op that reads the next count, so the host can
pad. The colour order (the WS2812 wants green, red, blue) is the caller's: the
program sends the bytes in the order they were queued, most significant bit
first, and does not interpret them.

After the count bytes the engine re-anchors its cursor to the current time with
``NOW``/``CURSORSET`` and pushes the first edge a small guard ahead, so the
frame starts cleanly however late the first queued byte arrived. Between the
bytes of one frame the cursor is never re-anchored and never advanced beyond a
symbol period, so the bytes form one unbroken bit stream with no latch between
them.
"""
from .. import PIN_COUNT, timing

#: Offset, in clock ticks, added to the re-anchored cursor before the first
#: edge of a frame. It covers the handful of instructions between reading the
#: clock and scheduling that edge so the edge is always placed in the future
#: rather than applied late. It is an instruction-latency guard, far smaller
#: than one symbol period, and it shifts only where a frame begins, never the
#: spacing of edges within it.
_EDGE_GUARD = 16


def _check_pin(pin: int) -> None:
    """Validate a protocol pin index.

    :param pin: the pin to drive.
    :raises ValueError: if ``pin`` is outside ``0..PIN_COUNT - 1``.
    """
    if not 0 <= pin < PIN_COUNT:
        raise ValueError(f"pin must be 0..{PIN_COUNT - 1}, got {pin}")


def _bit_loop(pin: int, label: str) -> str:
    """Return the eight-bit send loop body for one byte on ``pin``.

    The byte is expected left-justified in ``R0`` (bit 7 on bit 23) and ``R1``
    primed with the bit count. Each iteration shifts the next bit out through
    the carry, branches on it, and schedules the symbol's two edges. The body
    ends on a ``next_<label>:`` label; the caller appends the loop's ``DJNZ``.

    :param pin: the protocol pin to drive.
    :param label: a unique suffix making this block's labels distinct.
    """
    high0, low0 = timing.WS2812["0"]
    high1, low1 = timing.WS2812["1"]
    return f"""\
bit_{label}:
    SHL dst=0 amount=1
    BRC target=one_{label} condition=carry
    OUT pin={pin} level=1 offset=0
    ADVANCE ticks={high0}
    OUT pin={pin} level=0 offset=0
    ADVANCE ticks={low0}
    JMP target=next_{label}
one_{label}:
    OUT pin={pin} level=1 offset=0
    ADVANCE ticks={high1}
    OUT pin={pin} level=0 offset=0
    ADVANCE ticks={low1}
next_{label}:"""


def ws2812_tx(pin: int) -> str:
    """Return assembly for a WS2812 transmitter on ``pin``.

    The host frames a strip with a leading count byte followed by that many
    colour bytes (see the module docstring). The program sends one frame per
    count, most significant bit first, then holds the line low for the reset
    latch time and reads the next count.

    :param pin: the protocol pin to drive, ``0..PIN_COUNT - 1``.
    :returns: assembly source accepted by
        :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if ``pin`` is out of range.
    """
    _check_pin(pin)
    reset = timing.WS2812_RESET
    body = _bit_loop(pin, "tx")
    return f"""\
# WS2812 transmit. One frame per leading count byte, then that many colour
# bytes, most significant bit first, then a reset-low latch.
# R0 data shift register, R1 bit counter, R2 byte counter, R3 time scratch.
frame_start:
    PULL reg=2
    BREQ reg=2 value=0 target=frame_start
    NOW reg=3
    CURSORSET reg=3
    ADVANCE ticks={_EDGE_GUARD}
byte_loop:
    PULL reg=0
    SHL dst=0 amount=16
    LI reg=1 value=8
{body}
    DJNZ reg=1 target=bit_tx
    DJNZ reg=2 target=byte_loop
    ADVANCE ticks={reset}
    WAITTIME
    JMP target=frame_start
"""


#: The checked-in rainbow demonstration: three distinct GRB triples. Full-on
#: red, green and blue in the WS2812's green-red-blue byte order.
RAINBOW: tuple[tuple[int, int, int], ...] = (
    (0x00, 0xFF, 0x00),
    (0xFF, 0x00, 0x00),
    (0x00, 0x00, 0xFF),
)


def ws2812_rainbow(pin: int = 0) -> str:
    """Return a self-contained WS2812 program that plays :data:`RAINBOW` once.

    A thin wrapper over the same bit encoding as :func:`ws2812_tx`, but with the
    colour bytes baked in with ``LI`` instead of pulled from the host queue, so
    the program needs no host interaction: loaded and started, it drives three
    LEDs to the fixed rainbow, latches them, and halts. The per-bit loop is
    identical to the transmitter's, so it meets the same timing budget.

    :param pin: the protocol pin to drive, ``0..PIN_COUNT - 1``.
    :returns: assembly source accepted by
        :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if ``pin`` is out of range.
    """
    _check_pin(pin)
    reset = timing.WS2812_RESET
    body = _bit_loop(pin, "rb")
    sends = "\n".join(
        f"    LI reg=0 value={byte << 16}\n    CALL target=send_byte"
        for triple in RAINBOW
        for byte in triple
    )
    return f"""\
# WS2812 rainbow: three LEDs driven to fixed, distinct GRB triples, then a
# reset-low latch, then halt. The colour bytes are immediates, not pulled.
# R0 data shift register, R1 bit counter, R3 time scratch.
start:
    NOW reg=3
    CURSORSET reg=3
    ADVANCE ticks={_EDGE_GUARD}
{sends}
    ADVANCE ticks={reset}
    WAITTIME
    HALT
# Send R0 (left-justified byte) as eight WS2812 symbols, most significant first.
send_byte:
    LI reg=1 value=8
{body}
    DJNZ reg=1 target=bit_rb
    RET
"""
