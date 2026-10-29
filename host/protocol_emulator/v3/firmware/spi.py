"""SPI controller and target firmware for the V3 engines.

Both programs implement SPI in any of the four CPOL/CPHA modes on four separate
protocol pins (``sclk``, ``cs``, ``mosi``, ``miso``), eight bits per transfer,
most significant bit first, with chip select active low. The controller drives
``sclk``, ``cs`` and ``mosi`` and samples ``miso``; the target drives only
``miso`` and samples the other three. One byte is transferred in each direction
per host transmit-queue entry, and the byte received from the wire is pushed
with ``RX``.

Every clock timing the controller produces comes from
:data:`protocol_emulator.v3.timing.SPI_HALF`; no half period is written as a
literal here. The target drives no clock of its own and reacts to the
controller's edges, so it needs no bit-period constant at all.

Modes
-----

``mode`` is 0-3. CPOL (``mode >> 1``) is the idle level of ``sclk``; CPHA
(``mode & 1``) selects the sampling edge:

* CPHA 0 samples on the first (leading) edge of each clock period and shifts on
  the second (trailing) edge. The first bit of each byte is presented before the
  first leading edge -- at chip-select assertion.
* CPHA 1 shifts on the leading edge and samples on the trailing edge.

Both ends change their outgoing data on the shifting edge and latch the incoming
line on the sampling edge, so they are symmetric: the controller's ``mosi`` and
the target's ``miso`` move together and are sampled together.

Controller
----------

:func:`spi_controller` drives the three outputs and samples ``miso``. It pulls
one byte from the transmit queue, re-anchors the engine cursor to the current
time, and schedules every edge at ``cursor + guard``; the cursor advances by one
``SPI_HALF`` each half period, so the waveform is a clean 1 MHz clock regardless
of how many instructions a data-bit branch took. The data bit is chosen by a
branch because ``OUT`` takes a literal level, exactly as the UART transmitter
does, and for the same reason the branch asymmetry cannot jitter the clock. The
byte shifted in on ``miso`` is pushed with ``RX``.

Target
------

:func:`spi_target` never drives a clock. It waits for chip select to fall, then
follows the controller's clock edge by edge with ``WAITEDGE``: on each sampling
edge it shifts ``mosi`` into the input shift register, and on each shifting edge
it presents the next ``miso`` bit a few ticks later. Because it only ever acts
relative to a hardware edge, the fixed pad-to-engine input latency is absorbed:
the controller's half period (24 clocks at 1 MHz) is far longer than the
synchroniser and filter delay, so the bit the target presents on one edge is
stable long before the controller samples it on the next. The byte shifted in on
``mosi`` is pushed with ``RX``.

Receive record encoding
-----------------------

Each received byte is pushed with ``RX`` as a ``(value, meta)`` record:

* ``value`` is the eight received bits with the first-received (most
  significant) bit in bit 7.
* ``meta`` is always :data:`RX_META`. SPI has no framing or parity, so there is
  no error to flag; the meta byte is reserved and zero.
"""
from .. import PIN_COUNT, timing

#: Meta byte of every SPI receive record. SPI carries no framing or parity
#: error, so the meta byte has no flag defined and is always zero.
RX_META = 0x00

#: SPI clock rate, in hertz, whose half period is taken from
#: :data:`protocol_emulator.v3.timing.SPI_HALF`.
_RATE = 1_000_000

#: Offset, in clock ticks, added to the cursor for every scheduled edge. It
#: covers the few instructions between re-anchoring the cursor and the ``OUT``
#: so the edge is always scheduled in the future rather than applied late (the
#: target drives ``miso`` from a cursor re-anchored to the current time, so the
#: guard must clear the handful of select instructions). It is an
#: instruction-latency guard, not a protocol time, and is far smaller than one
#: half period at 1 MHz.
_EDGE_GUARD = 12

#: Horizon, in clock ticks, for the target's edge waits. ``WAITEDGE`` times out
#: when the cursor is reached, so the cursor is set this far ahead to make the
#: wait effectively open-ended; on a timeout the target simply re-arms. It is a
#: watchdog window, well inside the signed scheduling range.
_WAIT_HORIZON = 1 << 20

#: The MSB mask used to pick the bit a data pin drives, held in ``R3``.
_MSB_MASK = 0x80


def _check(sclk: int, cs: int, mosi: int, miso: int, mode: int) -> None:
    """Validate the four pins and the mode.

    :param sclk: clock pin.
    :param cs: chip-select pin.
    :param mosi: controller-out pin.
    :param miso: controller-in pin.
    :param mode: SPI mode.
    :raises ValueError: if a pin is outside ``0..PIN_COUNT - 1``, two pins
        coincide, or ``mode`` is not 0-3.
    """
    pins = {"sclk": sclk, "cs": cs, "mosi": mosi, "miso": miso}
    for name, pin in pins.items():
        if not 0 <= pin < PIN_COUNT:
            raise ValueError(f"{name} pin must be 0..{PIN_COUNT - 1}, got {pin}")
    if len(set(pins.values())) != len(pins):
        raise ValueError(f"SPI pins must be distinct, got {pins}")
    if mode not in (0, 1, 2, 3):
        raise ValueError(f"SPI mode must be 0-3, got {mode}")


def _polarities(cpol: int) -> tuple[str, str]:
    """Return the ``(leading, trailing)`` edge polarities for ``cpol``."""
    if cpol == 0:
        return "rising", "falling"
    return "falling", "rising"


def _select_bit(pin: int, label: str) -> list[str]:
    """Emit the branch that drives ``pin`` to the MSB of ``R0``.

    The caller must have ``R3`` holding :data:`_MSB_MASK` and leave ``R2`` free
    as scratch. ``label`` makes the two internal labels unique.
    """
    return [
        "    MOV dst=2 src=0",
        "    AND dst=2 src=3",
        f"    BREQ reg=2 value=0 target={label}_lo",
        f"    OUT pin={pin} level=1 offset={_EDGE_GUARD}",
        f"    JMP target={label}_done",
        f"{label}_lo:",
        f"    OUT pin={pin} level=0 offset={_EDGE_GUARD}",
        f"{label}_done:",
    ]


def spi_controller(sclk: int, cs: int, mosi: int, miso: int, mode: int) -> str:
    """Return assembly for an SPI controller in ``mode`` on the four pins.

    The program sends one byte per host transmit-queue entry on ``mosi`` and
    pushes the byte shifted in on ``miso`` with ``RX``. See the module docstring
    for the modes, the timing and the record encoding.

    :param sclk: clock pin to drive, ``0..PIN_COUNT - 1``.
    :param cs: chip-select pin to drive.
    :param mosi: controller-out pin to drive.
    :param miso: controller-in pin to sample.
    :param mode: SPI mode 0-3.
    :returns: assembly source accepted by :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if a pin is out of range, two pins coincide, or ``mode``
        is not 0-3.
    """
    _check(sclk, cs, mosi, miso, mode)
    cpol = mode >> 1
    cpha = mode & 1
    lead = cpol ^ 1
    half = timing.SPI_HALF[_RATE]
    g = _EDGE_GUARD

    lines = [
        f"# SPI controller, mode {mode} (CPOL={cpol} CPHA={cpha}), MSB first, 1 MHz.",
        f"# Drives sclk={sclk} cs={cs} mosi={mosi}; samples miso={miso}.",
        "# R0 MOSI/MISO shift register, R1 bit counter, R2 scratch, R3 the MSB mask.",
        "start:",
        "    PULL reg=0",
        "    CLEARIS",
        "    LI reg=1 value=8",
        f"    LI reg=3 value={_MSB_MASK}",
        "    NOW reg=2",
        "    CURSORSET reg=2",
        "    # a half period of headroom before chip select asserts, so the",
        "    # setup edges are scheduled ahead of the cursor, never late",
        f"    ADVANCE ticks={half}",
        f"    OUT pin={sclk} level={cpol} offset={g}",
        f"    OUT pin={cs} level=0 offset={g}",
    ]

    if cpha == 0:
        # Sample MISO on the leading edge, but as late in the leading half
        # period as possible (just before issuing the trailing/shift edge), so
        # a target reacting to the previous shift edge has a full period to
        # present the bit through its input and output latency.
        lines.append("    # present the first MOSI bit before the first leading edge")
        lines += _select_bit(mosi, "c0")
        lines.append("    SHL dst=0 amount=1")
        lines.append(f"    ADVANCE ticks={half}")
        lines += [
            "bit:",
            f"    OUT pin={sclk} level={lead} offset={g}",
            f"    ADVANCE ticks={half}",
            "    WAITTIME",
            f"    IN pin={miso} msb_first=1",
            f"    OUT pin={sclk} level={cpol} offset={g}",
        ]
        lines += _select_bit(mosi, "c1")
        lines += [
            "    SHL dst=0 amount=1",
            f"    ADVANCE ticks={half}",
            "    DJNZ reg=1 target=bit",
        ]
    else:
        # Sample MISO on the trailing edge, but as late in the trailing half
        # period as possible (just before the next leading/shift edge), for the
        # same reason.
        lines.append(f"    ADVANCE ticks={half}")
        lines += [
            "bit:",
            f"    OUT pin={sclk} level={lead} offset={g}",
        ]
        lines += _select_bit(mosi, "c1")
        lines += [
            "    SHL dst=0 amount=1",
            f"    ADVANCE ticks={half}",
            f"    OUT pin={sclk} level={cpol} offset={g}",
            f"    ADVANCE ticks={half}",
            "    WAITTIME",
            f"    IN pin={miso} msb_first=1",
            "    DJNZ reg=1 target=bit",
        ]

    lines += [
        "    WAITTIME",
        f"    OUT pin={cs} level=1 offset={g}",
        f"    RX meta={RX_META}",
        "    JMP target=start",
    ]
    return "\n".join(lines) + "\n"


def _target_select_bit(miso: int, label: str) -> list[str]:
    """Emit a target MISO drive: anchor the cursor to now, then drive the MSB."""
    return [
        "    NOW reg=2",
        "    CURSORSET reg=2",
    ] + _select_bit(miso, label)


def spi_target(sclk: int, cs: int, mosi: int, miso: int, mode: int) -> str:
    """Return assembly for an SPI target in ``mode`` on the four pins.

    The program drives only ``miso`` and samples ``sclk``, ``cs`` and ``mosi``.
    It pulls one reply byte per transfer from the transmit queue, drives it out
    on ``miso`` most significant bit first, and pushes the byte shifted in on
    ``mosi`` with ``RX``. See the module docstring for the modes and the record
    encoding.

    :param sclk: clock pin to sample.
    :param cs: chip-select pin to sample.
    :param mosi: controller-out pin to sample.
    :param miso: pin to drive with the reply.
    :param mode: SPI mode 0-3.
    :returns: assembly source accepted by :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if a pin is out of range, two pins coincide, or ``mode``
        is not 0-3.
    """
    _check(sclk, cs, mosi, miso, mode)
    cpol = mode >> 1
    cpha = mode & 1
    lead_pol, trail_pol = _polarities(cpol)
    horizon = _WAIT_HORIZON

    lines = [
        f"# SPI target, mode {mode} (CPOL={cpol} CPHA={cpha}), MSB first.",
        f"# Drives miso={miso}; samples sclk={sclk} cs={cs} mosi={mosi}.",
        "# R0 reply shift register, R1 bit counter, R2 scratch, R3 the MSB mask.",
        "start:",
        "    PULL reg=0",
        "    CLEARIS",
        "    LI reg=1 value=8",
        f"    LI reg=3 value={_MSB_MASK}",
        "    NOW reg=2",
        "    CURSORSET reg=2",
        f"    ADVANCE ticks={horizon}",
        f"    WAITEDGE pin={cs} polarity=falling",
        "    BRC target=start condition=timeout",
    ]

    if cpha == 0:
        lines.append("    # present the first MISO bit before the first leading edge")
        lines += _target_select_bit(miso, "t0")
        lines += [
            "    SHL dst=0 amount=1",
            f"    ADVANCE ticks={horizon}",
            "bit:",
            f"    WAITEDGE pin={sclk} polarity={lead_pol}",
            "    BRC target=start condition=timeout",
            f"    IN pin={mosi} msb_first=1",
            f"    WAITEDGE pin={sclk} polarity={trail_pol}",
            "    BRC target=start condition=timeout",
        ]
        lines += _target_select_bit(miso, "t1")
        lines += [
            "    SHL dst=0 amount=1",
            f"    ADVANCE ticks={horizon}",
            "    DJNZ reg=1 target=bit",
        ]
    else:
        lines += [
            "bit:",
            f"    WAITEDGE pin={sclk} polarity={lead_pol}",
            "    BRC target=start condition=timeout",
        ]
        lines += _target_select_bit(miso, "t1")
        lines += [
            "    SHL dst=0 amount=1",
            f"    ADVANCE ticks={horizon}",
            f"    WAITEDGE pin={sclk} polarity={trail_pol}",
            "    BRC target=start condition=timeout",
            f"    IN pin={mosi} msb_first=1",
            "    DJNZ reg=1 target=bit",
        ]

    lines += [
        f"    RX meta={RX_META}",
        "    JMP target=start",
    ]
    return "\n".join(lines) + "\n"
