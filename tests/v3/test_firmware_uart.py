"""UART firmware against the model and the independent UART peers.

The transmit program is run on the composed datapath and decoded with the
:class:`~protocol_emulator.v3.peers.UartReceiver`; the receive program is driven
with a :class:`~protocol_emulator.v3.peers.UartTransmitter` waveform and its
pushed records are checked. The loop cost is measured, never hardcoded, so a
change that cannot keep up at 1 Mbaud fails here.
"""
from pathlib import Path

import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, model, timing
from protocol_emulator.v3.firmware import uart_rx, uart_tx
from protocol_emulator.v3.firmware.uart import RX_META_FRAMING, RX_META_OK
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import UartReceiver, UartTransmitter
from tests.support.v3_firmware import EngineSpec, FirmwareBus, level_source

PIN = 0
BAUD = 115200
BIT = timing.UART[BAUD]
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "v3"


def _run_tx(data: bytes, baud: int = BAUD) -> tuple[FirmwareBus, int]:
    """Transmit ``data`` and return the bus and the observation end tick."""
    bit = timing.bit_cycles(baud)
    bus = FirmwareBus([EngineSpec(uart_tx(PIN, baud), owner=1 << PIN)])
    bus.engines[0].queues.push_tx(data)
    cycles = (len(data) + 1) * 11 * bit + 500
    bus.run(cycles)
    return bus, bus.base.now


def _frame(start: int, bit: int, byte: int, stop_level: int) -> list[tuple[int, int]]:
    """A raw 8N1 frame waveform with an explicit stop-bit level."""
    levels = [0] + [(byte >> i) & 1 for i in range(8)] + [stop_level]
    out = [(start - bit, 1)]
    for index, level in enumerate(levels):
        out.append((start + index * bit, level))
    out.append((start + len(levels) * bit, 1))
    return out


def _pop_all(bus: FirmwareBus) -> list[tuple[int, int]]:
    records = []
    while True:
        record = bus.engines[0].queues.pop_rx()
        if record is None:
            return records
        records.append(record)


def _measure_tx_cycles_per_bit(pin: int = PIN, baud: int = BAUD) -> int:
    """Measure the worst-case engine cycles between transmit bit commits.

    Sends the all-ones byte so every data bit takes the longer branch, and frees
    the pin slot every cycle so the throttling stall never hides the loop cost.
    The gap between successive scheduled commits is the per-bit work.
    """
    image = assemble(uart_tx(pin, baud))
    pins = [model.PinCell() for _ in range(8)]
    engine = model.Engine(pins=pins, owner=1 << pin)
    engine.load(image)
    engine.queues.push_tx(b"\xff")
    engine.start(0)
    scheduled: list[int] = []
    now = 0
    while len(scheduled) < 10 and now < 400:
        engine.step(now)
        if pins[pin].pending is not None:
            scheduled.append(now)
            pins[pin].pending = None
        now += 1
    gaps = [later - earlier for earlier, later in zip(scheduled, scheduled[1:])]
    assert gaps, "the transmit loop never scheduled a commit"
    return max(gaps)


# -- transmit -----------------------------------------------------------


def test_tx_program_fits_in_program_memory() -> None:
    image = assemble(uart_tx(PIN, BAUD))
    assert len(image.words) == PROGRAM_WORDS
    assert image.used < PROGRAM_WORDS


def test_tx_known_byte_decodes_with_no_violations_or_missed() -> None:
    bus, end = _run_tx(b"\x5a")
    result = UartReceiver(BIT).decode(bus.waveform(PIN), end=end)
    assert result.data == b"\x5a"
    assert result.violations == []
    assert not bus.any_missed()


def test_tx_multibyte_string_decodes_exactly() -> None:
    message = b"V3!"
    bus, end = _run_tx(message)
    result = UartReceiver(BIT).decode(bus.waveform(PIN), end=end)
    assert result.data == message
    assert result.violations == []
    assert not bus.any_missed()


def test_tx_loop_fits_the_1mbaud_budget() -> None:
    cycles_per_bit = _measure_tx_cycles_per_bit()
    assert cycles_per_bit <= timing.UART[1_000_000]


def test_tx_runs_at_1mbaud_and_decodes() -> None:
    baud = 1_000_000
    bit = timing.UART[baud]
    bus, end = _run_tx(b"\x5a\xa5", baud=baud)
    result = UartReceiver(bit).decode(bus.waveform(PIN), end=end)
    assert result.data == b"\x5a\xa5"
    assert result.violations == []
    assert not bus.any_missed()


# -- receive ------------------------------------------------------------


def test_rx_decodes_bytes_with_ok_meta() -> None:
    message = b"Z9"
    waveform = UartTransmitter(BIT).encode(message, start=50, gap_bits=2)
    bus = FirmwareBus([EngineSpec(uart_rx(PIN, BAUD), owner=0)])
    bus.set_stimulus(PIN, level_source(waveform))
    bus.run(waveform[-1][0] + 20 * BIT)
    records = _pop_all(bus)
    assert records == [(byte, RX_META_OK) for byte in message]


def test_rx_back_to_back_frames_are_both_received() -> None:
    message = b"\x00\xff\xa5"
    waveform = UartTransmitter(BIT).encode(message, start=50, gap_bits=0)
    bus = FirmwareBus([EngineSpec(uart_rx(PIN, BAUD), owner=0)])
    bus.set_stimulus(PIN, level_source(waveform))
    bus.run(waveform[-1][0] + 20 * BIT)
    records = _pop_all(bus)
    assert records == [(byte, RX_META_OK) for byte in message]


def test_rx_framing_error_sets_the_meta_flag_then_recovers() -> None:
    bad = _frame(200, BIT, 0x55, stop_level=0)
    good = UartTransmitter(BIT).encode(b"\x3c", start=bad[-1][0] + 2 * BIT)
    waveform = bad + good
    bus = FirmwareBus([EngineSpec(uart_rx(PIN, BAUD), owner=0)])
    bus.set_stimulus(PIN, level_source(waveform))
    bus.run(waveform[-1][0] + 20 * BIT)
    records = _pop_all(bus)
    assert records == [(0x55, RX_META_FRAMING), (0x3C, RX_META_OK)]


# -- overrun ------------------------------------------------------------


def test_overrun_sets_missed_and_still_emits_the_edge() -> None:
    baud = 6_000_000
    overrun_bit = timing.bit_cycles(baud)
    cycles_per_bit = _measure_tx_cycles_per_bit()
    assert overrun_bit < cycles_per_bit, "the chosen baud must outrun the loop"
    bus = FirmwareBus([EngineSpec(uart_tx(PIN, baud), owner=1 << PIN)])
    bus.engines[0].queues.push_tx(b"\xff\xff\xff")
    bus.run(3 * 12 * overrun_bit + 500)
    assert bus.any_missed(), "a late deadline must set the sticky missed flag"
    assert len(bus.waveform(PIN)) > 1, "the edge is late, not dropped"


# -- argument validation and checked-in examples ------------------------


@pytest.mark.parametrize("generator", [uart_tx, uart_rx])
def test_pin_out_of_range_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="pin must be"):
        generator(8, BAUD)


@pytest.mark.parametrize("generator", [uart_tx, uart_rx])
def test_unachievable_baud_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="rate error"):
        generator(PIN, 20_000_000)


def test_checked_in_tx_example_matches_the_generator() -> None:
    text = (EXAMPLES / "uart_tx_115200.asm").read_text(encoding="utf-8")
    assert text == uart_tx(0, 115200)


def test_checked_in_rx_example_matches_the_generator() -> None:
    text = (EXAMPLES / "uart_rx_115200.asm").read_text(encoding="utf-8")
    assert text == uart_rx(0, 115200)
