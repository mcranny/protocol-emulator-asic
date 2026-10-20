"""Protocol timing constants at 48 MHz.

Every value here is computed from :data:`protocol_emulator.v3.CLOCK_HZ`. None
are written as literals in firmware. :func:`cycles` refuses a conversion whose
rounding error exceeds the caller's tolerance, so an unachievable rate fails at
assembly time instead of producing a chip that is quietly off-spec.
"""
from . import CLOCK_HZ

TICK_SECONDS = 1.0 / CLOCK_HZ


def rate_error(seconds: float) -> float:
    """Signed relative error introduced by rounding ``seconds`` to whole cycles."""
    if seconds <= 0:
        raise ValueError("interval must be positive")
    exact = seconds * CLOCK_HZ
    rounded = round(exact)
    if rounded == 0:
        raise ValueError(f"{seconds} s is shorter than one clock cycle")
    return (rounded - exact) / exact


def cycles(seconds: float, tolerance: float = 0.001) -> int:
    """Convert ``seconds`` to whole clock cycles.

    :param seconds: the interval to convert.
    :param tolerance: the largest acceptable relative error, default 0.1%.
    :raises ValueError: if rounding would exceed ``tolerance``, or the interval
        is shorter than one cycle.
    """
    error = rate_error(seconds)
    if abs(error) > tolerance:
        raise ValueError(
            f"{seconds} s is {error:+.3%} rate error at {CLOCK_HZ} Hz, "
            f"over the {tolerance:.3%} tolerance"
        )
    return round(seconds * CLOCK_HZ)


def bit_cycles(baud: int, tolerance: float = 0.001) -> int:
    """Cycles per bit for ``baud``, rejecting rates that cannot be hit."""
    return cycles(1.0 / baud, tolerance)


#: Cycles per UART bit.
UART = {rate: bit_cycles(rate) for rate in (9600, 115200, 1_000_000)}

#: Cycles per SPI half period (one clock phase).
SPI_HALF = {rate: cycles(1.0 / (2 * rate)) for rate in (1_000_000,)}

#: I2C SCL ``(low, high)`` cycles.
#:
#: 100 kHz uses a symmetric 5.0 us split against minimums of 4.7 and 4.0 us.
#: 400 kHz uses 1.5 / 1.0 us against minimums of 1.3 and 0.6 us. The asymmetry
#: is deliberate: it leaves 400 ns of high-time headroom for the open-drain
#: rise, rather than meeting the 0.6 us minimum by a few nanoseconds.
I2C = {
    100_000: (cycles(5.0e-6), cycles(5.0e-6)),
    400_000: (cycles(1.5e-6), cycles(1.0e-6)),
}

#: WS2812 ``(high, low)`` cycles per symbol. Both symbols share a 1.25 us period.
#: Both symbols are 19.2 and 38.4 cycles exactly, so each rounds with a 1.04%
#: error. That is far outside the default tolerance and well inside the
#: datasheet's +/-150 ns window, so the tolerance is widened here and only here.
_WS2812_TOLERANCE = 0.02
WS2812 = {
    "0": (
        cycles(0.4e-6, _WS2812_TOLERANCE),
        cycles(1.25e-6) - cycles(0.4e-6, _WS2812_TOLERANCE),
    ),
    "1": (
        cycles(0.8e-6, _WS2812_TOLERANCE),
        cycles(1.25e-6) - cycles(0.8e-6, _WS2812_TOLERANCE),
    ),
}

#: WS2812 latch: hold the line low for at least this many cycles.
WS2812_RESET = cycles(300e-6)
