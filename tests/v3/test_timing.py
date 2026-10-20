"""Timing constants derive from 48 MHz and refuse silent rounding."""
import pytest

from protocol_emulator.v3 import timing


def test_exact_baud_rates_are_exact() -> None:
    assert timing.bit_cycles(9600) == 5000
    assert timing.bit_cycles(1_000_000) == 48


def test_115200_is_accepted_within_tolerance() -> None:
    assert timing.bit_cycles(115200) == 417
    assert abs(timing.rate_error(1 / 115200)) < 0.001


def test_a_rate_outside_tolerance_is_rejected() -> None:
    # 20 Mbaud is 2.4 cycles per bit; rounding to 2 is a 20% error.
    with pytest.raises(ValueError, match="rate error"):
        timing.bit_cycles(20_000_000)


def test_i2c_100k_meets_the_bus_minimums() -> None:
    low, high = timing.I2C[100_000]
    assert low == 240 and high == 240
    assert low / timing.CLOCK_HZ >= 4.7e-6
    assert high / timing.CLOCK_HZ >= 4.0e-6


def test_i2c_400k_keeps_margin_over_the_bus_minimums() -> None:
    low, high = timing.I2C[400_000]
    assert (low, high) == (72, 48)
    assert low / timing.CLOCK_HZ >= 1.3e-6
    # 1.0 us against a 0.6 us minimum: 400 ns of headroom for open-drain rise.
    assert high / timing.CLOCK_HZ - 0.6e-6 >= 390e-9


def test_ws2812_pulse_widths_land_inside_the_datasheet_windows() -> None:
    zero_high, zero_low = timing.WS2812["0"]
    one_high, one_low = timing.WS2812["1"]
    assert (zero_high, zero_low) == (19, 41)
    assert (one_high, one_low) == (38, 22)
    # Datasheet: T0H 0.4 us +/- 150 ns, T1H 0.8 us +/- 150 ns.
    assert abs(zero_high / timing.CLOCK_HZ - 0.4e-6) < 150e-9
    assert abs(one_high / timing.CLOCK_HZ - 0.8e-6) < 150e-9
    # Every bit is the same 1.25 us period.
    assert zero_high + zero_low == one_high + one_low == 60


def test_ws2812_reset_is_at_least_300_us() -> None:
    assert timing.WS2812_RESET >= 300e-6 * timing.CLOCK_HZ


def test_spi_half_periods_are_exact() -> None:
    assert timing.SPI_HALF[1_000_000] == 24
