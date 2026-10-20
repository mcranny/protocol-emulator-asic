"""The protocol peers decode ideal waveforms and report every perturbation.

Each protocol has at least one waveform built here, independently of the
peers' own generators, from the protocol rules. Perturbed waveforms must
produce a reported violation; nothing is accepted silently.
"""
from typing import TypeVar

import pytest

from protocol_emulator.v3 import peers, timing
from protocol_emulator.v3.peers import (
    I2cBus,
    I2cController,
    I2cOp,
    I2cTarget,
    Result,
    SpiBus,
    SpiController,
    SpiTarget,
    UartReceiver,
    UartTransmitter,
    Ws2812Receiver,
)

S = TypeVar("S")


def kinds(result: Result) -> set[str]:
    return {violation.kind for violation in result.violations}


def collapse(samples: list[tuple[int, S]]) -> list[tuple[int, S]]:
    """Reduce a waveform to its change points, later sample on a tick wins."""
    out: list[tuple[int, S]] = []
    for timestamp, state in samples:
        if out and out[-1][0] == timestamp:
            out.pop()
        if out and out[-1][1] == state:
            continue
        out.append((timestamp, state))
    return out


def merge(a: list[tuple[int, S]], b: list[tuple[int, S]]) -> list[tuple[int, S]]:
    return sorted(a + b, key=lambda sample: sample[0])


# ---------------------------------------------------------------- UART


def uart_slots(slots: list[int], period: int, start: int = 0) -> list[tuple[int, int]]:
    """Idle at ``start``, then one level per bit slot, then idle again."""
    out = [(start, 1)]
    for index, level in enumerate(slots):
        out.append((start + period * (index + 1), level))
    out.append((start + period * (len(slots) + 1), 1))
    return out


def uart_frame(value: int, stop: int = 1) -> list[int]:
    return [0] + [(value >> bit) & 1 for bit in range(8)] + [stop]


def stretch(samples: list[tuple[int, int]], factor: float) -> list[tuple[int, int]]:
    return [(round(timestamp * factor), level) for timestamp, level in samples]


RATES = [timing.UART[115200], timing.UART[1_000_000], timing.UART[9600]]


def test_chip_uart_periods_are_the_specified_ones() -> None:
    assert RATES == [417, 48, 5000]


@pytest.mark.parametrize("period", RATES)
def test_uart_receiver_decodes_ideal_frames(period: int) -> None:
    slots = uart_frame(0x55) + uart_frame(0x00) + uart_frame(0xFF) + uart_frame(0xA3)
    result = UartReceiver(period).decode(uart_slots(slots, period, start=1000))
    assert result.data == b"\x55\x00\xff\xa3"
    assert result.ok
    assert [event.kind for event in result.events] == ["byte"] * 4


def test_uart_start_bit_is_low_and_data_is_lsb_first() -> None:
    # 0x01 at 10 ticks per bit: start low, bit 0 high, bits 1-7 low, stop high.
    literal = [(0, 1), (10, 0), (20, 1), (30, 0), (100, 1), (110, 1)]
    assert UartReceiver(10).decode(literal).data == b"\x01"
    assert collapse(UartTransmitter(10).encode(b"\x01")) == collapse(literal)


@pytest.mark.parametrize("period", RATES)
def test_uart_transmitter_matches_the_hand_built_waveform(period: int) -> None:
    data = b"\x12\xfe\x00"
    slots = [level for value in data for level in uart_frame(value)]
    assert collapse(UartTransmitter(period).encode(data, start=77)) == collapse(
        uart_slots(slots, period, start=77)
    )


@pytest.mark.parametrize("period", RATES)
@pytest.mark.parametrize("gap", [0, 3])
def test_uart_round_trip(period: int, gap: int) -> None:
    data = bytes(range(0, 256, 17))
    result = UartReceiver(period).decode(UartTransmitter(period).encode(data, gap_bits=gap))
    assert result.data == data
    assert result.ok


def test_uart_bit_period_two_percent_long_is_reported() -> None:
    period = timing.UART[115200]
    slots = uart_frame(0xA5) + uart_frame(0x3C)
    long_wave = stretch(uart_slots(slots, period), 1.02)
    result = UartReceiver(period).decode(long_wave)
    assert result.data == b"\xa5\x3c"  # still decodable, but not accepted silently
    assert kinds(result) == {"bit_period"}
    assert len(result.violations) == 2


def test_uart_bit_period_two_percent_short_is_reported() -> None:
    period = timing.UART[115200]
    result = UartReceiver(period).decode(stretch(uart_slots(uart_frame(0x5A), period), 0.98))
    assert kinds(result) == {"bit_period"}


def test_uart_small_period_error_is_within_tolerance() -> None:
    period = timing.UART[115200]
    result = UartReceiver(period).decode(stretch(uart_slots(uart_frame(0x5A), period), 1.005))
    assert result.data == b"\x5a"
    assert result.ok


def test_uart_tolerance_is_a_parameter() -> None:
    period = 417
    long_wave = stretch(uart_slots(uart_frame(0x5A), period), 1.02)
    assert UartReceiver(period, tolerance=0.05).decode(long_wave).ok


def test_uart_missing_stop_bit_is_reported_and_byte_withheld() -> None:
    period = 417
    result = UartReceiver(period).decode(uart_slots(uart_frame(0x55, stop=0) + [1], period))
    assert kinds(result) == {"framing"}
    assert result.data == b""
    assert [(event.kind, event.value) for event in result.events] == [("framing_error", 0x55)]


def test_uart_recovers_after_a_framing_error() -> None:
    period = 417
    slots = uart_frame(0x55, stop=0) + [1] + uart_frame(0x42)
    result = UartReceiver(period).decode(uart_slots(slots, period))
    assert kinds(result) == {"framing"}
    assert result.data == b"\x42"


def test_uart_data_bits_that_look_like_a_start_do_not_restart_a_bad_frame() -> None:
    # A bad stop bit with zero data: the low line must not be read as new frames.
    period = 48
    result = UartReceiver(period).decode(uart_slots(uart_frame(0x00, stop=0) + [1] * 3, period))
    assert result.data == b""
    assert len([v for v in result.violations if v.kind == "framing"]) == 1


def test_uart_glitch_in_idle_is_reported_and_its_effect_is_visible() -> None:
    period = 417
    wave = [(0, 1), (100, 0), (102, 1)] + uart_slots(uart_frame(0x55), period, start=1000)
    result = UartReceiver(period).decode(wave)
    assert result.data == b"\x55"
    assert kinds(result) == {"glitch", "false_start"}
    stripped = UartReceiver(period).decode(wave, strip_glitches=True)
    assert stripped.data == b"\x55"
    assert kinds(stripped) == {"glitch"}


def test_uart_glitch_on_a_bit_centre_corrupts_data_unless_stripped() -> None:
    period = 48
    base = uart_slots(uart_frame(0x55), period)
    # Data bit 1 of 0x55 is 0. Its centre is 2.5 bits after the start edge.
    centre = period + round(2.5 * period)
    glitch = [(centre - 1, 1), (centre + 1, 0)]
    result = UartReceiver(period).decode(merge(base, glitch))
    assert result.data == b"\x57"  # a real receiver samples the pulse
    assert "glitch" in kinds(result)
    assert not result.ok
    stripped = UartReceiver(period).decode(merge(base, glitch), strip_glitches=True)
    assert stripped.data == b"\x55"
    assert kinds(stripped) == {"glitch"}


def test_uart_two_short_stop_bits_are_reported() -> None:
    # Review 3: 0.6-bit stop bits, back to back, used to decode with ok=True.
    period = 417
    frame_ticks = [(0, 1)]
    now = period
    for value in (0x55, 0xA3):
        for level in uart_frame(value)[:-1]:  # start and data bits
            frame_ticks.append((now, level))
            now += period
        frame_ticks.append((now, 1))  # stop bit, only 0.6 bit long
        now += round(0.6 * period)
    frame_ticks.append((now, 1))
    result = UartReceiver(period).decode(frame_ticks)
    assert result.data == b"\x55\xa3"
    assert "stop_short" in kinds(result)
    assert not result.ok


def test_uart_full_stop_bit_is_not_stop_short() -> None:
    period = 417
    slots = uart_frame(0x55) + uart_frame(0xA3)
    assert UartReceiver(period).decode(uart_slots(slots, period)).ok


def test_uart_line_starting_low_is_reported() -> None:
    period = 417
    wave = [(0, 0), (500, 1)] + uart_slots(uart_frame(0x55), period, start=1000)[1:]
    result = UartReceiver(period).decode(wave)
    assert "idle_level" in kinds(result)
    assert not result.ok
    assert "idle_level" in kinds(UartReceiver(period).decode([(0, 0), (10, 0)]))


def test_uart_trace_ending_on_the_stop_edge_keeps_its_byte() -> None:
    # Review M1: the last sample is the stop bit's own rising edge.
    period = 417
    wave = uart_slots(uart_frame(0x55), period)[:-1]
    assert wave[-1] == (10 * period, 1)
    result = UartReceiver(period).decode(wave)
    assert result.data == b"\x55"
    assert result.ok
    # A frame cut much shorter is still truncated.
    assert kinds(UartReceiver(period).decode(wave[:5])) == {"truncated"}


def test_uart_pulse_at_the_filter_window_qualifies_but_is_a_false_start() -> None:
    period = 417
    window = peers.FILTER_SAMPLES
    wave = [(0, 1), (100, 0), (100 + window, 1)] + uart_slots(
        uart_frame(0x55), period, start=1000
    )
    result = UartReceiver(period).decode(wave)
    assert result.data == b"\x55"
    assert kinds(result) == {"false_start"}  # three ticks qualify: not a glitch


def test_filter_window_is_three_samples() -> None:
    assert peers.FILTER_SAMPLES == 3


def test_uart_truncated_frame_is_reported() -> None:
    period = 417
    wave = uart_slots(uart_frame(0x55), period)
    result = UartReceiver(period).decode(wave, end=5 * period)
    assert kinds(result) == {"truncated"}
    assert result.data == b""


def test_uart_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        UartReceiver(417).decode([])
    with pytest.raises(ValueError):
        UartReceiver(417).decode([(10, 1), (5, 0)])
    with pytest.raises(ValueError):
        UartReceiver(417).decode([(0, 2)])
    with pytest.raises(ValueError):
        UartReceiver(0)
    with pytest.raises(ValueError):
        UartTransmitter(0)


# ---------------------------------------------------------------- SPI


def spi_dense(mode: int, data: bytes, half: int) -> list[tuple[int, SpiBus]]:
    """Per-tick SPI waveform from the mode rules, one chip-select assertion.

    CS falls at tick ``half``. With phase ``k = (tick - half) // half`` the
    clock is active on odd ``k`` up to ``2n - 1``, CS stays low through
    ``k = 2n`` and rises at ``k = 2n + 1``. CPHA 0 changes data on trailing
    edges (even ``k``), CPHA 1 on leading edges (odd ``k``).
    """
    cpol, cpha = mode >> 1, mode & 1
    bits = [(byte >> (7 - position)) & 1 for byte in data for position in range(8)]
    count = len(bits)
    out: list[tuple[int, SpiBus]] = []
    for tick in range((2 * count + 2) * half + 1):
        if tick < half:
            out.append((tick, SpiBus(cpol, 1, 0, 0)))
            continue
        phase = (tick - half) // half
        chip_select = 0 if phase <= 2 * count else 1
        active = 1 if (phase % 2 == 1 and phase <= 2 * count - 1) else 0
        if cpha == 0:
            mosi = bits[min(phase // 2, count - 1)]
        else:
            mosi = 0 if phase == 0 else bits[min((phase - 1) // 2, count - 1)]
        out.append((tick, SpiBus(cpol ^ active, chip_select, mosi, 0)))
    return out


@pytest.mark.parametrize("mode", range(4))
def test_spi_target_decodes_the_hand_built_waveform(mode: int) -> None:
    result = SpiTarget(mode).decode(spi_dense(mode, b"\xa5\x3c", 3))
    assert result.data == b"\xa5\x3c"
    assert result.ok
    assert [event.kind for event in result.events if event.kind.startswith("cs")] == [
        "cs_assert",
        "cs_release",
    ]


@pytest.mark.parametrize("mode", range(4))
@pytest.mark.parametrize("half", [3, timing.SPI_HALF[1_000_000]])
def test_spi_controller_matches_the_hand_built_waveform(mode: int, half: int) -> None:
    controller = SpiController(mode, half)
    assert collapse(controller.transfer(b"\xa5\x3c")) == collapse(
        spi_dense(mode, b"\xa5\x3c", half)
    )


@pytest.mark.parametrize("mode", range(4))
def test_spi_controller_to_target_round_trip(mode: int) -> None:
    payload = bytes([0x00, 0xFF, 0x81, 0x7E, 0xC3])
    wave = SpiController(mode, timing.SPI_HALF[1_000_000]).transfer(payload, start=500)
    result = SpiTarget(mode).decode(wave)
    assert result.data == payload
    assert result.aux == b"\x00" * len(payload)
    assert result.ok


@pytest.mark.parametrize("mode", range(4))
def test_spi_target_reply_is_sampled_on_the_correct_edge(mode: int) -> None:
    wave = SpiController(mode, 24).transfer(b"\x12\x34")
    answered = SpiTarget(mode).respond(wave, b"\xc3\x5a")
    result = SpiController(mode, 24).decode(answered)
    assert result.data == b"\x12\x34"
    assert result.aux == b"\xc3\x5a"
    assert result.ok
    assert answered[-1][1].miso == 0


@pytest.mark.parametrize("mode", range(4))
def test_spi_back_to_back_transfers(mode: int) -> None:
    controller = SpiController(mode, 24)
    first = controller.transfer(b"\x11\x22")
    second = controller.transfer(b"\x33", start=first[-1][0] + 48)
    result = SpiTarget(mode).decode(first + second)
    assert result.data == b"\x11\x22\x33"
    assert result.ok
    assert [event.kind for event in result.events].count("cs_assert") == 2


@pytest.mark.parametrize("mode", range(4))
def test_spi_cs_released_mid_byte_is_reported(mode: int) -> None:
    controller = SpiController(mode, 24)
    wave = controller.transfer(b"\xff")
    cpol = mode >> 1
    cut = wave[: 2 + 2 * 4]  # idle, CS assert, then four complete clock periods
    cut.append((cut[-1][0] + 24, SpiBus(cpol, 1, 0, 0)))
    result = SpiTarget(mode).decode(cut)
    assert kinds(result) == {"cs_mid_byte"}
    assert result.data == b""
    follow = controller.transfer(b"\x77", start=cut[-1][0] + 48)
    recovered = SpiTarget(mode).decode(cut + follow)
    assert recovered.data == b"\x77"
    assert kinds(recovered) == {"cs_mid_byte"}


@pytest.mark.parametrize("mode", range(4))
def test_spi_observation_ending_mid_byte_is_reported(mode: int) -> None:
    wave = SpiController(mode, 24).transfer(b"\xff")
    result = SpiTarget(mode).decode(wave[: 2 + 2 * 3])
    assert kinds(result) == {"truncated"}


@pytest.mark.parametrize("mode", range(4))
def test_spi_glitch_on_sclk_is_reported_and_ignored(mode: int) -> None:
    wave = SpiController(mode, 24).transfer(b"\x5a")
    cpol = mode >> 1
    # Inside the idle gap before CS, and between the edges while selected.
    held = wave[3][0]  # a tick where CS is low and SCLK is mid-period
    state = wave[3][1]
    glitches = [
        (5, SpiBus(cpol ^ 1, 1, 0, 0)),
        (7, SpiBus(cpol, 1, 0, 0)),
        (held + 5, SpiBus(state.sclk ^ 1, 0, state.mosi, 0)),
        (held + 7, state),
    ]
    glitched = merge(wave, glitches)
    result = SpiTarget(mode).decode(glitched)
    assert "glitch" in kinds(result)
    assert not result.ok
    assert result.data != b"\x5a"  # the extra clock edge is seen, not hidden
    stripped = SpiTarget(mode).decode(glitched, strip_glitches=True)
    assert stripped.data == b"\x5a"
    assert kinds(stripped) == {"glitch"}
    assert len(stripped.violations) == 2


@pytest.mark.parametrize("mode", range(4))
def test_spi_respond_keeps_and_reports_a_glitch(mode: int) -> None:
    # Review 1: respond() used to deglitch, so respond -> decode came back ok.
    wave = SpiController(mode, 24).transfer(b"\x5a")
    held = wave[3]
    glitched = merge(wave, [(held[0] + 5, SpiBus(held[1].sclk ^ 1, 0, held[1].mosi, 0)),
                            (held[0] + 7, held[1])])
    answered = SpiTarget(mode).respond(glitched, b"\xc3")
    assert [(t, s[:3]) for t, s in answered] == [(t, s[:3]) for t, s in glitched]
    assert "glitch" in kinds(SpiTarget(mode).decode(answered))


@pytest.mark.parametrize(("width", "is_glitch"), [(2, True), (3, False)])
def test_spi_glitch_width_boundary(width: int, is_glitch: bool) -> None:
    wave = SpiController(0, 24).transfer(b"\x5a")
    pulse = [(5, SpiBus(1, 1, 0, 0)), (5 + width, SpiBus(0, 1, 0, 0))]  # CS high: harmless
    result = SpiTarget(0).decode(merge(wave, pulse))
    assert ("glitch" in kinds(result)) is is_glitch
    assert result.data == b"\x5a"


MODE_PAIRS = [(a, b) for a in range(4) for b in range(4) if a != b]


@pytest.mark.parametrize(("sent", "decoded"), MODE_PAIRS)
def test_spi_decoding_with_the_wrong_mode_is_reported(sent: int, decoded: int) -> None:
    wave = spi_dense(sent, b"\xa5", 3)
    assert SpiTarget(sent).decode(wave).ok
    result = SpiTarget(decoded).decode(wave)
    assert not result.ok
    cpol_differs = (sent >> 1) != (decoded >> 1)
    assert ("clock_idle" in kinds(result)) is cpol_differs
    if not cpol_differs:
        assert "data_at_sample_edge" in kinds(result)


def test_spi_start_state_must_be_idle() -> None:
    # Review 5: CS low at the start, and SCLK away from CPOL at the start.
    selected = [(0, SpiBus(0, 0, 0, 0)), (10, SpiBus(0, 1, 0, 0))]
    assert "idle_level" in kinds(SpiTarget(0).decode(selected))
    wrong_clock = [(0, SpiBus(1, 1, 0, 0)), (10, SpiBus(1, 1, 0, 0))]
    assert "idle_level" in kinds(SpiTarget(0).decode(wrong_clock))
    assert SpiTarget(2).decode(wrong_clock).ok


@pytest.mark.parametrize("mode", range(4))
def test_spi_cs_release_with_sclk_off_idle_is_reported(mode: int) -> None:
    # Review 6: CS rises while SCLK is at the active level.
    cpol = mode >> 1
    wave = SpiController(mode, 24).transfer(b"")
    assert SpiTarget(mode).decode(wave).ok
    release = wave[-1][0]
    bad = wave[:-1] + [(release - 10, SpiBus(cpol ^ 1, 0, 0, 0)), (release, SpiBus(cpol ^ 1, 1, 0, 0))]
    result = SpiTarget(mode).decode(bad)
    assert "clock_idle" in kinds(result)  # the stray edge may also be a partial byte


def test_spi_data_changing_on_the_sampling_edge_is_reported() -> None:
    # Mode 0 samples on the rising edge; MOSI changes on that same tick.
    wave = [
        (0, SpiBus(0, 1, 0, 0)),
        (10, SpiBus(0, 0, 0, 0)),
        (20, SpiBus(1, 0, 1, 0)),
        (30, SpiBus(0, 0, 1, 0)),
        (40, SpiBus(0, 1, 1, 0)),
    ]
    result = SpiTarget(0).decode(wave)
    assert "data_at_sample_edge" in kinds(result)


def test_spi_cs_and_clock_on_one_tick_is_reported() -> None:
    wave = [(0, SpiBus(0, 1, 0, 0)), (10, SpiBus(1, 0, 0, 0)), (20, SpiBus(0, 0, 0, 0))]
    assert "cs_sclk_same_tick" in kinds(SpiTarget(0).decode(wave))


def test_spi_clock_activity_while_deselected_is_ignored() -> None:
    wave = [
        (0, SpiBus(0, 1, 0, 0)),
        (10, SpiBus(1, 1, 1, 0)),
        (20, SpiBus(0, 1, 1, 0)),
        (30, SpiBus(1, 1, 0, 0)),
    ]
    result = SpiTarget(0).decode(wave)
    assert result.data == b"" and result.ok


def test_spi_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        SpiTarget(4)
    with pytest.raises(ValueError):
        SpiController(0, 0)
    with pytest.raises(ValueError):
        SpiTarget(0).decode([(0, SpiBus(0, 1, 0, 0)), (1, SpiBus(0, 1, 2, 0))])


# ---------------------------------------------------------------- I2C

#: (tLOW, tHIGH) minimums in 48 MHz ticks, from the I2C-bus specification:
#: ceil(4.7 us * 48), ceil(4.0 us * 48); ceil(1.3 us * 48), ceil(0.6 us * 48).
MINIMUM = {100_000: (226, 192), 400_000: (63, 29)}


def i2c_ideal(
    payload: bytes, acks: list[bool], low: int, high: int, start: int = 0
) -> list[tuple[int, I2cBus]]:
    """Resolved bus for START, bytes with ACK/NACK slots, STOP.

    Clock ``n`` is low from ``f + n * period`` and high from ``+ low``; SDA is
    set in the middle of the low phase.
    """
    period = low + high
    slots: list[int] = []
    for value, acked in zip(payload, acks):
        slots += [(value >> (7 - bit)) & 1 for bit in range(8)] + [0 if acked else 1]
    begin = start + low
    fall = begin + high
    out = [(start, I2cBus(1, 1)), (begin, I2cBus(1, 0)), (fall, I2cBus(0, 0))]
    for index, level in enumerate(slots):
        base = fall + index * period
        out += [
            (base + low // 2, I2cBus(0, level)),
            (base + low, I2cBus(1, level)),
            (base + period, I2cBus(0, level)),
        ]
    end = fall + len(slots) * period
    out += [
        (end + low // 2, I2cBus(0, 0)),
        (end + low, I2cBus(1, 0)),
        (end + low + high, I2cBus(1, 1)),
        (end + low + high + low, I2cBus(1, 1)),
    ]
    return out


def test_i2c_minimums_match_the_standard() -> None:
    assert peers.I2C_MINIMUM_CYCLES == MINIMUM


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_i2c_target_decodes_ack_and_nack(rate: int) -> None:
    low, high = timing.I2C[rate]
    wave = i2c_ideal(b"\xa0\x3c", [True, False], low, high, start=50)
    result = I2cTarget(rate).decode(wave)
    assert result.data == b"\xa0\x3c"
    assert result.ok
    assert [event.kind for event in result.events] == [
        "start",
        "byte",
        "ack",
        "byte",
        "nack",
        "stop",
    ]
    assert [event.value for event in result.events if event.kind == "byte"] == [0xA0, 0x3C]


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_i2c_controller_matches_the_hand_built_waveform(rate: int) -> None:
    low, high = timing.I2C[rate]
    ops = [I2cOp("start"), I2cOp("write", 0xA0), I2cOp("write", 0x3C), I2cOp("stop")]
    generated = I2cController(low, high).transaction(ops, start=50)
    expected = i2c_ideal(b"\xa0\x3c", [False, False], low, high, start=50)
    assert collapse(generated) == collapse(expected)


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_i2c_controller_with_target_ack_round_trip(rate: int) -> None:
    low, high = timing.I2C[rate]
    ops = [I2cOp("start"), I2cOp("write", 0xA0), I2cOp("write", 0x3C), I2cOp("stop")]
    bus = I2cController(low, high).transaction(ops)
    acked = I2cTarget(rate).respond(bus)
    result = I2cTarget(rate).decode(acked)
    assert result.data == b"\xa0\x3c"
    assert [event.kind for event in result.events if event.kind in ("ack", "nack")] == [
        "ack",
        "ack",
    ]
    assert result.ok
    nacked = I2cTarget(rate).decode(I2cTarget(rate).respond(bus, ack=[True, False]))
    assert [event.kind for event in nacked.events if event.kind in ("ack", "nack")] == [
        "ack",
        "nack",
    ]
    absent = I2cTarget(rate).decode(bus)
    assert [event.kind for event in absent.events if event.kind in ("ack", "nack")] == [
        "nack",
        "nack",
    ]


def test_i2c_repeated_start_read_and_stop() -> None:
    low, high = timing.I2C[400_000]
    ops = [
        I2cOp("start"),
        I2cOp("write", 0xA0),
        I2cOp("write", 0x10),
        I2cOp("start"),
        I2cOp("write", 0xA1),
        I2cOp("read", 1),
        I2cOp("read", 0),
        I2cOp("stop"),
    ]
    bus = I2cController(low, high).transaction(ops)
    answered = I2cTarget(400_000).respond(bus, ack=[True, True, True, False, False])
    result = I2cTarget(400_000).decode(answered)
    assert result.ok
    assert [event.kind for event in result.events] == [
        "start",
        "byte", "ack", "byte", "ack",
        "restart",
        "byte", "ack",
        "byte", "ack",
        "byte", "nack",
        "stop",
    ]  # fmt: skip
    # Read bytes are driven by the target; with none present SDA reads high.
    assert result.data == b"\xa0\x10\xa1\xff\xff"


def test_i2c_controller_read_acks_with_sda_low() -> None:
    bus = I2cController(240, 240).transaction([I2cOp("start"), I2cOp("read", 1), I2cOp("stop")])
    events = I2cTarget(100_000).decode(bus).events
    assert [event.kind for event in events] == ["start", "byte", "ack", "stop"]


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_i2c_scl_low_time_is_checked_against_the_minimum(rate: int) -> None:
    minimum_low, minimum_high = MINIMUM[rate]
    ops = [I2cOp("start"), I2cOp("write", 0x55), I2cOp("stop")]
    at_minimum = I2cController(minimum_low, minimum_high + 40).transaction(ops)
    assert I2cTarget(rate).decode(at_minimum).ok
    below = I2cController(minimum_low - 1, minimum_high + 40).transaction(ops)
    result = I2cTarget(rate).decode(below)
    assert kinds(result) == {"tLOW"}
    assert result.data == b"\x55"


@pytest.mark.parametrize("rate", [100_000, 400_000])
def test_i2c_scl_high_time_is_checked_against_the_minimum(rate: int) -> None:
    minimum_low, minimum_high = MINIMUM[rate]
    ops = [I2cOp("start"), I2cOp("write", 0x55), I2cOp("stop")]
    at_minimum = I2cController(minimum_low + 40, minimum_high).transaction(ops)
    assert I2cTarget(rate).decode(at_minimum).ok
    below = I2cController(minimum_low + 40, minimum_high - 1).transaction(ops)
    result = I2cTarget(rate).decode(below)
    assert kinds(result) == {"tHIGH"}
    assert result.data == b"\x55"


def test_i2c_chip_timing_clears_the_minimums_but_400k_timing_fails_at_100k() -> None:
    ops = [I2cOp("start"), I2cOp("write", 0x55), I2cOp("stop")]
    for rate in (100_000, 400_000):
        bus = I2cController(*timing.I2C[rate]).transaction(ops)
        assert I2cTarget(rate).decode(bus).ok
    fast = I2cController(*timing.I2C[400_000]).transaction(ops)
    assert kinds(I2cTarget(100_000).decode(fast)) == {"tLOW", "tHIGH"}


def test_i2c_clock_stretching_is_not_a_violation() -> None:
    bus = I2cController(2000, 240).transaction([I2cOp("start"), I2cOp("write", 1), I2cOp("stop")])
    assert I2cTarget(100_000).decode(bus).ok


def _bit_pulses(count: int, sda: int) -> list[tuple[int, I2cBus]]:
    """``count`` clock pulses of 300 ticks low, 300 high, from tick 1300."""
    out: list[tuple[int, I2cBus]] = []
    for index in range(count):
        base = 1300 + index * 600
        out += [
            (base + 150, I2cBus(0, sda)),
            (base + 300, I2cBus(1, sda)),
            (base + 600, I2cBus(0, sda)),
        ]
    return out


def test_i2c_stop_in_the_middle_of_a_byte_is_reported() -> None:
    bus = [(0, I2cBus(1, 1)), (1000, I2cBus(1, 0)), (1300, I2cBus(0, 0))]
    bus += _bit_pulses(3, 1)
    end = bus[-1][0]
    bus += [(end + 150, I2cBus(0, 0)), (end + 300, I2cBus(1, 0)), (end + 600, I2cBus(1, 1))]
    result = I2cTarget(100_000).decode(bus)
    assert kinds(result) == {"partial_byte"}
    assert result.data == b""
    assert result.events[-1].kind == "stop"


def test_i2c_start_in_the_middle_of_a_byte_is_reported() -> None:
    bus = [(0, I2cBus(1, 1)), (1000, I2cBus(1, 0)), (1300, I2cBus(0, 0))]
    bus += _bit_pulses(2, 1)
    end = bus[-1][0]
    bus += [(end + 150, I2cBus(0, 1)), (end + 300, I2cBus(1, 1)), (end + 600, I2cBus(1, 0))]
    result = I2cTarget(100_000).decode(bus)
    assert kinds(result) == {"partial_byte", "unterminated"}
    assert [event.kind for event in result.events] == ["start", "restart"]


def test_i2c_observation_ending_mid_byte_is_reported() -> None:
    bus = [(0, I2cBus(1, 1)), (1000, I2cBus(1, 0)), (1300, I2cBus(0, 0))] + _bit_pulses(2, 0)
    assert kinds(I2cTarget(100_000).decode(bus)) == {"truncated", "unterminated"}


def test_i2c_transaction_without_stop_is_unterminated() -> None:
    # Review 4: a complete write whose STOP is missing used to decode ok=True.
    ops = [I2cOp("start"), I2cOp("write", 0xA0), I2cOp("write", 0x3C)]
    bus = I2cController(240, 240).transaction(ops)
    result = I2cTarget(100_000).decode(bus)
    assert result.data == b"\xa0\x3c"
    assert kinds(result) == {"unterminated"}
    closed = I2cController(240, 240).transaction(ops + [I2cOp("stop")])
    assert I2cTarget(100_000).decode(closed).ok


def test_i2c_unterminated_after_a_repeated_start_without_stop() -> None:
    ops = [I2cOp("start"), I2cOp("write", 0xA0), I2cOp("start"), I2cOp("write", 0xA1)]
    result = I2cTarget(100_000).decode(I2cController(240, 240).transaction(ops))
    assert kinds(result) == {"unterminated"}


def test_i2c_start_state_must_be_idle() -> None:
    bus = [(0, I2cBus(0, 1)), (500, I2cBus(1, 1))]
    assert "idle_level" in kinds(I2cTarget(100_000).decode(bus))
    bus = [(0, I2cBus(1, 0)), (500, I2cBus(1, 1))]
    assert "idle_level" in kinds(I2cTarget(100_000).decode(bus))


def test_i2c_respond_keeps_and_reports_a_glitch() -> None:
    # Review 1: respond() used to deglitch, so respond -> decode came back ok.
    bus = I2cController(240, 240).transaction(
        [I2cOp("start"), I2cOp("write", 0xA5), I2cOp("stop")]
    )
    glitched = merge(bus, [(bus[3][0] + 20, I2cBus(0, 0)), (bus[3][0] + 22, bus[3][1])])
    answered = I2cTarget(100_000).respond(glitched)
    assert "glitch" in kinds(I2cTarget(100_000).decode(answered))
    clean = I2cTarget(100_000).respond(bus)
    assert I2cTarget(100_000).decode(clean).ok


@pytest.mark.parametrize(("width", "is_glitch"), [(2, True), (3, False)])
def test_i2c_glitch_width_boundary(width: int, is_glitch: bool) -> None:
    bus = I2cController(240, 240).transaction(
        [I2cOp("start"), I2cOp("write", 0xA5), I2cOp("stop")]
    )
    fall = bus[2][0]  # SCL low from here; SDA is quiet at the start of the low phase
    pulse = [(fall + 10, I2cBus(0, 1)), (fall + 10 + width, I2cBus(0, 0))]
    result = I2cTarget(100_000).decode(merge(bus, pulse))
    assert ("glitch" in kinds(result)) is is_glitch
    assert result.data == b"\xa5"


def test_i2c_scl_and_sda_changing_together_is_reported() -> None:
    bus = [
        (0, I2cBus(1, 1)),
        (1000, I2cBus(1, 0)),
        (1300, I2cBus(0, 0)),
        (1900, I2cBus(1, 1)),
    ]
    assert "simultaneous_edges" in kinds(I2cTarget(100_000).decode(bus))


def test_i2c_sda_glitch_while_scl_is_high_is_seen_unless_stripped() -> None:
    bus = I2cController(240, 240).transaction(
        [I2cOp("start"), I2cOp("write", 0xA5), I2cOp("stop")]
    )
    # The first clock pulse is high from fall + low to fall + low + high.
    high_start = next(t for t, state in bus if state.scl == 1 and t > bus[2][0])
    glitch = [
        (high_start + 100, I2cBus(1, 0)),  # SDA is high for the first bit of 0xA5
        (high_start + 102, I2cBus(1, 1)),
    ]
    result = I2cTarget(100_000).decode(merge(bus, glitch))
    assert "glitch" in kinds(result)
    assert not result.ok
    assert result.data != b"\xa5"  # the pulse reads as a START and STOP
    stripped = I2cTarget(100_000).decode(merge(bus, glitch), strip_glitches=True)
    assert stripped.data == b"\xa5"
    assert kinds(stripped) == {"glitch"}
    assert [event.kind for event in stripped.events].count("start") == 1


def _bus_clear(pulses: int) -> list[tuple[int, I2cBus]]:
    bus = [(0, I2cBus(1, 1)), (1300, I2cBus(0, 1))] + _bit_pulses(pulses, 1)
    end = bus[-1][0]
    return bus + [(end + 150, I2cBus(0, 0)), (end + 300, I2cBus(1, 0)), (end + 600, I2cBus(1, 1))]


def test_i2c_clocks_outside_a_frame_are_reported_unless_a_bus_clear_is_allowed() -> None:
    # Review 4: nine clocks with SDA high and no START used to decode ok=True.
    bus = _bus_clear(9)
    result = I2cTarget(100_000).decode(bus)
    assert kinds(result) == {"clock_outside_frame"}
    assert result.data == b""
    allowed = I2cTarget(100_000).decode(bus, allow_bus_clear=True)
    assert allowed.ok
    assert "clock_outside_frame" in kinds(I2cTarget(100_000).decode(_bus_clear(8)))
    assert I2cTarget(100_000).decode(_bus_clear(7)).ok


def test_i2c_bus_clear_pulses_are_timed_but_not_decoded() -> None:
    bus = _bus_clear(9)
    result = I2cTarget(100_000).decode(bus, allow_bus_clear=True)
    assert result.data == b"" and result.ok
    short = [(0, I2cBus(1, 1)), (1000, I2cBus(0, 1)), (1100, I2cBus(1, 1)), (1400, I2cBus(0, 1)), (1600, I2cBus(1, 1))]
    assert kinds(I2cTarget(100_000).decode(short)) == {"tLOW"}


def test_i2c_rejects_bad_arguments() -> None:
    with pytest.raises(ValueError):
        I2cTarget(1_000_000)
    with pytest.raises(ValueError):
        I2cController(1, 1)
    with pytest.raises(ValueError):
        I2cController(240, 240).transaction([I2cOp("write", 1)])
    with pytest.raises(ValueError):
        I2cController(240, 240).transaction([I2cOp("start"), I2cOp("nonsense")])


# ---------------------------------------------------------------- WS2812

#: (high, low) ticks per symbol, from the design specification table.
ZERO = (19, 41)
ONE = (38, 22)
RESET_TICKS = 14_400


def ws_wave(
    symbols: list[tuple[int, int]], gap: int = RESET_TICKS + 100, start: int = 200
) -> list[tuple[int, int]]:
    """Line low at tick 0; each symbol is a high then a low; then ``gap`` low.

    The last symbol's own low time is replaced by ``gap``.
    """
    out = [(0, 0)]
    now = start
    for index, (high, low) in enumerate(symbols):
        out += [(now, 1), (now + high, 0)]
        now += high + (gap if index == len(symbols) - 1 else low)
    out.append((now, 0))
    return out


def ws_bits(data: bytes) -> list[tuple[int, int]]:
    return [ONE if (value >> (7 - bit)) & 1 else ZERO for value in data for bit in range(8)]


def test_chip_ws2812_symbols_are_the_specified_ones() -> None:
    assert timing.WS2812["0"] == ZERO and timing.WS2812["1"] == ONE
    assert timing.WS2812_RESET == RESET_TICKS


def test_ws2812_decodes_bit_zero_bit_one_and_reset() -> None:
    for data in (b"\x00", b"\xff", b"\x80", b"\x01", b"\xa5"):
        result = Ws2812Receiver().decode(ws_wave(ws_bits(data)))
        assert result.data == data
        assert result.ok
        assert [event.kind for event in result.events] == ["byte", "reset"]


def test_ws2812_decodes_a_three_led_frame_msb_first() -> None:
    grb = bytes([0x12, 0x34, 0x56, 0xFF, 0x00, 0x80, 0x01, 0x02, 0x03])
    result = Ws2812Receiver().decode(ws_wave(ws_bits(grb)))
    assert result.data == grb
    assert result.ok
    assert result.events[-1].kind == "reset"


def test_ws2812_high_time_tolerance_is_150_ns() -> None:
    # 150 ns is 7.2 ticks. Nominal highs are 19.2 and 38.4 ticks.
    cases = (
        # (symbol, low, accepted highs, rejected highs)
        (0, 41, (13, 26), (11, 27)),
        (1, 22, (32, 45), (31, 46)),
    )
    for symbol, low, accepted, rejected in cases:
        for high in accepted:
            result = Ws2812Receiver().decode(ws_wave([(high, low)] + ws_bits(b"\x00")[1:]))
            assert "high_time" not in kinds(result), (symbol, high)
            assert result.data[0] >> 7 == symbol
        for high in rejected:
            result = Ws2812Receiver().decode(ws_wave([(high, low)] + ws_bits(b"\x00")[1:]))
            assert "high_time" in kinds(result), (symbol, high)


def test_ws2812_out_of_tolerance_high_is_reported_but_still_classified() -> None:
    too_long_zero = ws_bits(b"\x00")
    too_long_zero[3] = (28, 41)
    result = Ws2812Receiver().decode(ws_wave(too_long_zero))
    assert kinds(result) == {"high_time"}
    assert result.data == b"\x00"
    too_short_one = ws_bits(b"\xff")
    too_short_one[5] = (30, 22)
    result = Ws2812Receiver().decode(ws_wave(too_short_one))
    assert kinds(result) == {"high_time"}
    assert result.data == b"\xff"


def test_ws2812_out_of_tolerance_low_is_reported() -> None:
    symbols = ws_bits(b"\x00")
    symbols[2] = (19, 30)  # nominal 40.8 ticks
    assert kinds(Ws2812Receiver().decode(ws_wave(symbols))) == {"low_time"}
    symbols = ws_bits(b"\xff")
    symbols[2] = (38, 31)  # nominal 21.6 ticks
    assert kinds(Ws2812Receiver().decode(ws_wave(symbols))) == {"low_time"}


def test_ws2812_long_low_below_reset_is_not_a_latch() -> None:
    symbols = ws_bits(b"\xaa") + ws_bits(b"\x55")
    wave = ws_wave(symbols[:8])
    # Split the stream with a gap of 5000 ticks: too long for a bit, too short to latch.
    second = ws_wave(symbols[8:], start=wave[-2][0] + 5000)
    result = Ws2812Receiver().decode(wave[:-1] + second[1:])
    assert "low_time" in kinds(result)
    assert [event.kind for event in result.events].count("reset") == 1


def test_ws2812_reset_threshold_is_300_us() -> None:
    wave = ws_wave(ws_bits(b"\x5a"), gap=RESET_TICKS)
    assert [event.kind for event in Ws2812Receiver().decode(wave).events][-1] == "reset"
    short = ws_wave(ws_bits(b"\x5a"), gap=RESET_TICKS - 1)
    result = Ws2812Receiver().decode(short)
    assert [event.kind for event in result.events] == ["byte"]
    assert result.data == b"\x5a"


def test_ws2812_reset_is_detected_with_an_explicit_end() -> None:
    wave = ws_wave(ws_bits(b"\x5a"), gap=10)
    last_fall = wave[-2][0]
    assert "reset" not in [e.kind for e in Ws2812Receiver().decode(wave).events]
    result = Ws2812Receiver().decode(wave, end=last_fall + RESET_TICKS)
    assert [event.kind for event in result.events] == ["byte", "reset"]


def test_ws2812_two_latches() -> None:
    first = ws_wave(ws_bits(b"\x11\x22"))
    second = ws_wave(ws_bits(b"\x33"), start=first[-1][0] + 100)
    result = Ws2812Receiver().decode(first + second[1:])
    assert result.data == b"\x11\x22\x33"
    assert [event.kind for event in result.events] == ["byte", "byte", "reset", "byte", "reset"]
    assert result.ok


def test_ws2812_partial_byte_at_reset_is_reported() -> None:
    result = Ws2812Receiver().decode(ws_wave(ws_bits(b"\xff")[:5]))
    assert kinds(result) == {"partial_byte"}
    assert result.data == b""


def test_ws2812_line_ending_high_is_reported() -> None:
    wave = ws_wave(ws_bits(b"\xff"))
    result = Ws2812Receiver().decode(wave[:-3] + [(wave[-3][0], 1)])
    assert "truncated" in kinds(result)


def test_ws2812_glitch_inside_a_pulse_is_seen_unless_stripped() -> None:
    wave = ws_wave(ws_bits(b"\xff"))
    glitch = [(wave[1][0] + 10, 0), (wave[1][0] + 11, 1)]
    result = Ws2812Receiver().decode(merge(wave, glitch))
    assert "glitch" in kinds(result)
    assert not result.ok
    assert result.data != b"\xff"  # the dip splits one symbol into two
    stripped = Ws2812Receiver().decode(merge(wave, glitch), strip_glitches=True)
    assert stripped.data == b"\xff"
    assert kinds(stripped) == {"glitch"}


@pytest.mark.parametrize(("width", "is_glitch"), [(2, True), (3, False)])
def test_ws2812_glitch_width_boundary(width: int, is_glitch: bool) -> None:
    wave = ws_wave(ws_bits(b"\xff"))
    dip = [(wave[1][0] + 10, 0), (wave[1][0] + 10 + width, 1)]
    result = Ws2812Receiver().decode(merge(wave, dip))
    assert ("glitch" in kinds(result)) is is_glitch
    assert not result.ok  # a 3-tick dip is a real (and malformed) symbol


def test_ws2812_line_starting_high_is_reported_not_treated_as_a_rise() -> None:
    wave = ws_wave(ws_bits(b"\xff"))
    wave[0] = (0, 1)  # high from tick 0, falls at the end of the first symbol
    result = Ws2812Receiver().decode(wave)
    assert "idle_level" in kinds(result)
    assert not result.ok
    assert result.data == b""  # the first pulse's start is unknown, so it is not a bit


def test_ws2812_require_reset() -> None:
    no_gap = ws_wave(ws_bits(b"\x5a"), gap=10)
    assert Ws2812Receiver().decode(no_gap).ok
    result = Ws2812Receiver().decode(no_gap, require_reset=True)
    assert kinds(result) == {"no_reset"}
    assert result.data == b"\x5a"
    latched = ws_wave(ws_bits(b"\x5a"))
    assert Ws2812Receiver().decode(latched, require_reset=True).ok
    # Symbols after the last reset are unlatched even if an earlier frame was.
    first = ws_wave(ws_bits(b"\x11"))
    second = ws_wave(ws_bits(b"\x22"), gap=10, start=first[-1][0] + 100)
    result = Ws2812Receiver().decode(first + second[1:], require_reset=True)
    assert kinds(result) == {"no_reset"}
    assert Ws2812Receiver().decode([(0, 0), (5, 0)], require_reset=True).ok


def test_ws2812_constants_follow_the_datasheet() -> None:
    assert (peers.WS2812_T0H_NS, peers.WS2812_T1H_NS) == (400, 800)
    assert (peers.WS2812_T0L_NS, peers.WS2812_T1L_NS) == (850, 450)
    assert peers.WS2812_TOLERANCE_NS == 150
    assert peers.WS2812_RESET_NS == 300_000
