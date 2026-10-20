"""WS2812 firmware against the model and the independent WS2812 peer.

The transmitter is run on the composed datapath and its single wire is decoded
with the :class:`~protocol_emulator.v3.peers.Ws2812Receiver`. Every emitted
symbol's high time and period are measured off the model waveform and checked
against the WS2812B datasheet windows directly, rather than against the
generator's own timing table, so a wrong waveform fails here. The per-bit loop
cost is measured, never hardcoded, so a change that cannot keep up with the
1.25 us symbol period fails too.
"""
from pathlib import Path

import pytest

from protocol_emulator.v3 import CLOCK_HZ, PROGRAM_WORDS, model
from protocol_emulator.v3.firmware.ws2812 import RAINBOW, ws2812_rainbow, ws2812_tx
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import Ws2812Receiver
from tests.support.v3_firmware import EngineSpec, FirmwareBus

PIN = 0
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "v3"

# WS2812B datasheet, independent of the firmware's timing table. Nanoseconds.
T0H_NS = 400
T1H_NS = 800
PERIOD_NS = 1_250
TOLERANCE_NS = 150
RESET_NS = 300_000

#: The reset latch low must cover this many clock ticks (300 us at 48 MHz).
RESET_TICKS = RESET_NS * CLOCK_HZ // 10**9

#: Engine clocks per symbol period (1.25 us at 48 MHz): the per-bit budget.
BUDGET_CYCLES = round(PERIOD_NS * 1e-9 * CLOCK_HZ)


def _ticks_to_ns(ticks: int) -> float:
    """Convert clock ticks to nanoseconds at the system clock."""
    return ticks * 1e9 / CLOCK_HZ


def _frame(data: bytes) -> bytes:
    """Prefix ``data`` with its WS2812 count byte."""
    return bytes([len(data)]) + data


def _run_tx(data: bytes) -> tuple[FirmwareBus, int]:
    """Transmit one ``data`` frame; return the bus and the observation end."""
    bus = FirmwareBus([EngineSpec(ws2812_tx(PIN), owner=1 << PIN)], pull=0)
    bus.engines[0].queues.push_tx(_frame(data))
    bus.run(len(data) * 8 * BUDGET_CYCLES + RESET_TICKS + 2_000)
    return bus, bus.base.now


def _symbols(wave: list[tuple[int, int]]) -> list[tuple[int, int | None]]:
    """Return ``(high, period)`` tick pairs for each symbol in ``wave``.

    A symbol is a rising edge, the next (falling) edge, and the following rise.
    ``period`` is ``None`` for the last symbol, which has no following rise.
    """
    rises = [i for i, (_, level) in enumerate(wave) if level == 1]
    symbols: list[tuple[int, int | None]] = []
    for position, index in enumerate(rises):
        if index + 1 >= len(wave):
            continue
        high = wave[index + 1][0] - wave[index][0]
        period: int | None = None
        if position + 1 < len(rises):
            period = wave[rises[position + 1]][0] - wave[index][0]
        symbols.append((high, period))
    return symbols


def _measure_cycles_per_bit(byte: int) -> int:
    """Worst-case engine cycles between successive rising edges for ``byte``.

    Queues a one-byte frame and frees the pin slot every cycle so the throttle
    never hides the loop cost; the gap between successive scheduled rising
    commits is the per-bit work. ``0x00`` exercises the zero-symbol branch and
    ``0xFF`` the one-symbol branch.
    """
    image = assemble(ws2812_tx(PIN))
    pins = [model.PinCell() for _ in range(8)]
    engine = model.Engine(pins=pins, owner=1 << PIN)
    engine.load(image)
    engine.queues.push_tx(bytes([1, byte]))
    engine.start(0)
    rises: list[int] = []
    now = 0
    while len(rises) < 9 and now < 4_000:
        engine.step(now)
        pending = pins[PIN].pending
        if pending is not None:
            if pending.value == 1:
                rises.append(now)
            pins[PIN].pending = None
        now += 1
    gaps = [later - earlier for earlier, later in zip(rises, rises[1:])]
    assert gaps, "the transmit loop never scheduled a rising edge"
    return max(gaps)


# -- program size -------------------------------------------------------


def test_tx_program_fits_in_program_memory() -> None:
    image = assemble(ws2812_tx(PIN))
    assert len(image.words) == PROGRAM_WORDS
    assert image.used < PROGRAM_WORDS


def test_rainbow_program_fits_in_program_memory() -> None:
    image = assemble(ws2812_rainbow(PIN))
    assert len(image.words) == PROGRAM_WORDS
    assert image.used < PROGRAM_WORDS


# -- waveform timing ----------------------------------------------------


def test_every_symbol_high_and_period_are_inside_the_datasheet_windows() -> None:
    # A byte of each kind so both symbol widths appear.
    bus, _ = _run_tx(b"\x00\xff")
    symbols = _symbols(bus.waveform(PIN))
    assert len(symbols) == 16, f"expected 16 symbols, measured {len(symbols)}"
    for high_ticks, period_ticks in symbols:
        high_ns = _ticks_to_ns(high_ticks)
        nominal = T1H_NS if abs(high_ns - T1H_NS) < abs(high_ns - T0H_NS) else T0H_NS
        assert abs(high_ns - nominal) <= TOLERANCE_NS, (
            f"high {high_ns:.1f} ns is outside {nominal} +/- {TOLERANCE_NS} ns"
        )
        if period_ticks is not None:
            period_ns = _ticks_to_ns(period_ticks)
            assert abs(period_ns - PERIOD_NS) <= TOLERANCE_NS, (
                f"period {period_ns:.1f} ns is outside {PERIOD_NS} +/- {TOLERANCE_NS} ns"
            )


def test_reset_low_after_the_frame_is_at_least_300_us() -> None:
    bus, end = _run_tx(b"\x12\x34\x56")
    wave = bus.waveform(PIN)
    last_fall = max(tick for tick, level in wave if level == 0)
    assert end - last_fall >= RESET_TICKS, (
        f"reset low {end - last_fall} ticks is under {RESET_TICKS}"
    )


# -- decode against the oracle ------------------------------------------


def test_transmitter_bytes_decode_exactly_with_no_violations() -> None:
    data = bytes(byte for triple in RAINBOW for byte in triple)
    bus, end = _run_tx(data)
    result = Ws2812Receiver().decode(bus.waveform(PIN), end=end)
    assert result.data == data, f"decoded {result.data.hex()}, expected {data.hex()}"
    assert result.violations == [], f"unexpected violations {result.violations}"
    assert not bus.any_missed()


def test_single_led_decodes_exactly() -> None:
    data = b"\x0f\xf0\xaa"
    bus, end = _run_tx(data)
    result = Ws2812Receiver().decode(bus.waveform(PIN), end=end)
    assert result.data == data
    assert result.violations == []


def test_zero_count_is_a_no_op_then_the_next_count_is_sent() -> None:
    data = b"\x81\x7e\x24"
    bus = FirmwareBus([EngineSpec(ws2812_tx(PIN), owner=1 << PIN)], pull=0)
    bus.engines[0].queues.push_tx(bytes([0]) + _frame(data))
    bus.run(len(data) * 8 * BUDGET_CYCLES + RESET_TICKS + 2_000)
    result = Ws2812Receiver().decode(bus.waveform(PIN), end=bus.base.now)
    assert result.data == data
    assert result.violations == []
    assert not bus.any_missed()


# -- rainbow demonstration ----------------------------------------------


def test_rainbow_plays_the_fixed_triples() -> None:
    bus = FirmwareBus([EngineSpec(ws2812_rainbow(PIN), owner=1 << PIN)], pull=0)
    bus.run(len(RAINBOW) * 3 * 8 * BUDGET_CYCLES + RESET_TICKS + 3_000)
    result = Ws2812Receiver().decode(bus.waveform(PIN), end=bus.base.now)
    expected = bytes(byte for triple in RAINBOW for byte in triple)
    assert result.data == expected, f"decoded {result.data.hex()}, expected {expected.hex()}"
    assert result.violations == []
    assert not bus.any_missed()


# -- per-bit budget -----------------------------------------------------


def test_zero_bit_loop_fits_the_symbol_budget() -> None:
    assert _measure_cycles_per_bit(0x00) <= BUDGET_CYCLES


def test_one_bit_loop_fits_the_symbol_budget() -> None:
    assert _measure_cycles_per_bit(0xFF) <= BUDGET_CYCLES


# -- argument validation and the checked-in example --------------------


@pytest.mark.parametrize("generator", [ws2812_tx, ws2812_rainbow])
def test_pin_out_of_range_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="pin must be"):
        generator(8)


def test_checked_in_rainbow_example_matches_the_generator() -> None:
    text = (EXAMPLES / "ws2812_rainbow.asm").read_text(encoding="utf-8")
    assert text == ws2812_rainbow(0)
