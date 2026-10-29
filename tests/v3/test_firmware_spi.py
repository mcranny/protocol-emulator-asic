"""SPI firmware against the model and the independent SPI peers.

The controller program is run on the composed datapath and its wire is decoded
with :class:`~protocol_emulator.v3.peers.SpiTarget`; the target program is driven
with a :class:`~protocol_emulator.v3.peers.SpiController` waveform and both the
byte it receives and the reply it drives on MISO are checked. The mode matrix
runs all four CPOL/CPHA combinations with a controller and a target on one bus,
asserts a byte comes back identical in each direction, and asserts the target's
input sample lands on the edge the mode requires, so a wrong CPHA is caught.
"""
from pathlib import Path

import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, timing
from protocol_emulator.v3.firmware import spi_controller, spi_target
from protocol_emulator.v3.firmware.spi import RX_META
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import SpiBus, SpiController, SpiTarget
from tests.support.v3_firmware import EngineSpec, FirmwareBus, level_source

SCLK, CS, MOSI, MISO = 0, 1, 2, 3
HALF = timing.SPI_HALF[1_000_000]
MODES = (0, 1, 2, 3)
EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "v3"

#: Clocks a single-byte transfer takes end to end, with headroom for the
#: half-period setup, eight 48-clock periods and the trailing chip-select idle.
_BYTE_CLOCKS = 48 * 14


def _owner(*pins: int) -> int:
    mask = 0
    for pin in pins:
        mask |= 1 << pin
    return mask


def _pull(mode: int) -> int:
    """Bus pull levels: every line idles high except SCLK, which idles at CPOL."""
    pull = 0xFF
    if (mode >> 1) == 0:
        pull &= ~(1 << SCLK)
    return pull


def _bus_samples(bus: FirmwareBus) -> list[tuple[int, SpiBus]]:
    """Merge the four recorded pin waveforms into a bus-level sample list."""
    sources = {pin: level_source(bus.waveform(pin)) for pin in (SCLK, CS, MOSI, MISO)}
    times = sorted({tick for pin in (SCLK, CS, MOSI, MISO) for tick, _ in bus.waveform(pin)})
    return [
        (
            tick,
            SpiBus(sources[SCLK](tick), sources[CS](tick), sources[MOSI](tick), sources[MISO](tick)),
        )
        for tick in times
    ]


def _run_controller(mode: int, data: bytes) -> FirmwareBus:
    """Run the controller alone (MISO left idle) and return the bus."""
    bus = FirmwareBus(
        [EngineSpec(spi_controller(SCLK, CS, MOSI, MISO, mode), owner=_owner(SCLK, CS, MOSI))],
        pull=_pull(mode),
    )
    bus.engines[0].queues.push_tx(data)
    bus.run(_BYTE_CLOCKS * len(data) + 200)
    return bus


def _run_target(mode: int, mosi_data: bytes, reply: bytes) -> FirmwareBus:
    """Drive the target with a controller-peer waveform; return the bus.

    The firmware target transfers one byte per chip-select assertion, so the
    stimulus is one single-byte transfer per byte with an idle gap between, the
    way the controller firmware frames a transfer.
    """
    stimulus: list[tuple[int, SpiBus]] = []
    start = 40
    for byte in mosi_data:
        transfer = SpiController(mode, HALF).transfer(bytes([byte]), start=start)
        stimulus += transfer
        start = transfer[-1][0] + 4 * HALF
    bus = FirmwareBus(
        [EngineSpec(spi_target(SCLK, CS, MOSI, MISO, mode), owner=_owner(MISO))],
        pull=_pull(mode),
    )
    for pin, field in ((SCLK, 0), (CS, 1), (MOSI, 2)):
        bus.set_stimulus(pin, level_source([(tick, state[field]) for tick, state in stimulus]))
    bus.engines[0].queues.push_tx(reply)
    bus.run(stimulus[-1][0] + _BYTE_CLOCKS)
    return bus


def _run_duplex(mode: int, to_target: bytes, to_controller: bytes) -> FirmwareBus:
    """Run a controller (engine 0) and a target (engine 1) on one bus."""
    bus = FirmwareBus(
        [
            EngineSpec(spi_controller(SCLK, CS, MOSI, MISO, mode), owner=_owner(SCLK, CS, MOSI)),
            EngineSpec(spi_target(SCLK, CS, MOSI, MISO, mode), owner=_owner(MISO)),
        ],
        pull=_pull(mode),
    )
    bus.engines[0].queues.push_tx(to_target)
    bus.engines[1].queues.push_tx(to_controller)
    bus.run(_BYTE_CLOCKS * max(len(to_target), len(to_controller)) + 300)
    return bus


def _pop_all(bus: FirmwareBus, engine: int) -> list[tuple[int, int]]:
    records = []
    while True:
        record = bus.engines[engine].queues.pop_rx()
        if record is None:
            return records
        records.append(record)


# -- controller: generates a spec-valid waveform ------------------------


def test_controller_program_fits_in_program_memory() -> None:
    image = assemble(spi_controller(SCLK, CS, MOSI, MISO, 0))
    assert len(image.words) == PROGRAM_WORDS
    assert image.used < PROGRAM_WORDS


@pytest.mark.parametrize("mode", MODES)
def test_controller_waveform_decodes_with_no_violations(mode: int) -> None:
    data = b"\x5a\xc3"
    bus = _run_controller(mode, data)
    result = SpiTarget(mode).decode(_bus_samples(bus))
    assert result.data == data, f"mode {mode}: decoded MOSI {result.data!r}, expected {data!r}"
    assert result.violations == [], f"mode {mode}: unexpected violations {result.violations}"
    assert not bus.any_missed()


@pytest.mark.parametrize("mode", MODES)
def test_controller_rejected_by_the_wrong_mode_decoder(mode: int) -> None:
    """A decoder for a different CPHA does not recover the byte, proving the
    waveform carries the mode it claims."""
    bus = _run_controller(mode, b"\x5a")
    good = SpiTarget(mode).decode(_bus_samples(bus))
    wrong = SpiTarget(mode ^ 1).decode(_bus_samples(bus))
    assert good.data == b"\x5a"
    assert wrong.data != b"\x5a" or bool(wrong.violations)


# -- target: receives MOSI, drives the reply on MISO --------------------


@pytest.mark.parametrize("mode", MODES)
def test_target_receives_mosi_and_drives_reply(mode: int) -> None:
    mosi = b"\x5a\xc3"
    reply = b"\xa5\x3c"
    bus = _run_target(mode, mosi, reply)
    assert _pop_all(bus, 0) == [(byte, RX_META) for byte in mosi]
    result = SpiTarget(mode).decode(_bus_samples(bus))
    assert result.data == mosi, f"mode {mode}: target read MOSI {result.data!r}"
    assert result.aux == reply, f"mode {mode}: target drove MISO {result.aux!r}, expected {reply!r}"
    assert result.violations == [], f"mode {mode}: unexpected violations {result.violations}"
    assert not bus.any_missed()


# -- mode matrix: both directions and the sampling edge -----------------


def _sclk_edges(bus: FirmwareBus) -> list[tuple[int, int]]:
    """The SCLK waveform without the opening idle seed: real edges only."""
    return bus.waveform(SCLK)[1:]


def _sample_level(mode: int) -> int:
    """The SCLK level established by the edge the target samples on in ``mode``.

    CPHA 0 samples on the leading edge (SCLK driven to NOT CPOL); CPHA 1 on the
    trailing edge (SCLK driven to CPOL).
    """
    cpol, cpha = mode >> 1, mode & 1
    return (cpol ^ 1) if cpha == 0 else cpol


def _run_duplex_tracing_target_samples(
    mode: int, to_target: bytes, to_controller: bytes, target_mode: int | None = None
) -> tuple[FirmwareBus, list[int]]:
    """Run a duplex transfer, recording the tick of every target ``IN``.

    ``target_mode`` defaults to ``mode``; set it differently to mismatch the
    controller and target modes deliberately.
    """
    if target_mode is None:
        target_mode = mode
    bus = FirmwareBus(
        [
            EngineSpec(spi_controller(SCLK, CS, MOSI, MISO, mode), owner=_owner(SCLK, CS, MOSI)),
            EngineSpec(spi_target(SCLK, CS, MOSI, MISO, target_mode), owner=_owner(MISO)),
        ],
        pull=_pull(mode),
    )
    bus.engines[0].queues.push_tx(to_target)
    bus.engines[1].queues.push_tx(to_controller)

    sampled_at: list[int] = []
    target = bus.engines[1]
    original = target._handlers["IN"]

    def traced(ops: dict[str, int], now: int) -> str:
        sampled_at.append(now)
        return original(ops, now)

    target._handlers["IN"] = traced
    bus.run(_BYTE_CLOCKS * max(len(to_target), len(to_controller)) + 300)
    return bus, sampled_at


def _edge_before(edges: list[tuple[int, int]], tick: int) -> int:
    """Return the SCLK level established by the last edge at or before ``tick``."""
    prior = [level for edge_tick, level in edges if edge_tick <= tick]
    assert prior, f"sample at {tick} precedes every SCLK edge"
    return prior[-1]


@pytest.mark.parametrize("mode", MODES)
def test_mode_matrix_round_trip_and_sampling_edge(mode: int) -> None:
    to_target = 0x5A
    to_controller = 0xA5
    bus, sampled_at = _run_duplex_tracing_target_samples(
        mode, bytes([to_target]), bytes([to_controller])
    )

    # Both directions come back identical.
    assert _pop_all(bus, 0) == [(to_controller, RX_META)], f"mode {mode}: controller receive"
    assert _pop_all(bus, 1) == [(to_target, RX_META)], f"mode {mode}: target receive"
    assert not bus.any_missed()

    # The wire carries the mode it claims.
    result = SpiTarget(mode).decode(_bus_samples(bus))
    assert result.data == bytes([to_target])
    assert result.aux == bytes([to_controller])
    assert result.violations == []

    # The target sampled MOSI on the edge the mode requires. Check the most
    # recent SCLK edge before each sample.
    expected_level = _sample_level(mode)
    edges = _sclk_edges(bus)
    assert len(sampled_at) == 8, f"mode {mode}: {len(sampled_at)} samples, expected 8"
    for sample_tick in sampled_at:
        assert _edge_before(edges, sample_tick) == expected_level, (
            f"mode {mode}: sample at {sample_tick} did not follow the "
            f"{'leading' if (mode & 1) == 0 else 'trailing'} edge (level {expected_level})"
        )


@pytest.mark.parametrize("mode", MODES)
def test_wrong_cpha_target_samples_the_wrong_edge(mode: int) -> None:
    """A target whose CPHA is flipped samples on the other edge of every clock,
    which is exactly what the mode-matrix sampling-edge assertion rejects. The
    sampling edge, not the decoded byte, is the reliable discriminator: with a
    line held for a whole period a single byte can survive the wrong CPHA, but
    the sample provably lands on the wrong edge."""
    bus, sampled_at = _run_duplex_tracing_target_samples(
        mode, b"\x5a", b"\xa5", target_mode=mode ^ 1
    )
    edges = _sclk_edges(bus)
    assert len(sampled_at) == 8
    correct_level = _sample_level(mode)
    flipped_level = _sample_level(mode ^ 1)
    assert flipped_level != correct_level
    for sample_tick in sampled_at:
        assert _edge_before(edges, sample_tick) == flipped_level, (
            f"mode {mode}: a CPHA-flipped target should sample after the "
            f"opposite edge (level {flipped_level})"
        )


# -- duplex: both directions in one transaction -------------------------


def test_duplex_exchanges_a_byte_each_way() -> None:
    bus = _run_duplex(0, b"\x2d", b"\xd4")
    assert _pop_all(bus, 0) == [(0xD4, RX_META)], "controller must receive the target's byte"
    assert _pop_all(bus, 1) == [(0x2D, RX_META)], "target must receive the controller's byte"
    assert not bus.any_missed()
    result = SpiTarget(0).decode(_bus_samples(bus))
    assert result.data == b"\x2d"
    assert result.aux == b"\xd4"
    assert result.violations == []


def test_duplex_multibyte_each_way() -> None:
    to_target = b"\x01\x80\xff"
    to_controller = b"\xaa\x55\x0f"
    bus = _run_duplex(0, to_target, to_controller)
    assert _pop_all(bus, 0) == [(byte, RX_META) for byte in to_controller]
    assert _pop_all(bus, 1) == [(byte, RX_META) for byte in to_target]
    assert not bus.any_missed()


# -- argument validation and the checked-in examples --------------------


@pytest.mark.parametrize("generator", [spi_controller, spi_target])
def test_pin_out_of_range_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="pin must be"):
        generator(8, 1, 2, 3, 0)


@pytest.mark.parametrize("generator", [spi_controller, spi_target])
def test_coincident_pins_are_rejected(generator) -> None:
    with pytest.raises(ValueError, match="distinct"):
        generator(0, 0, 2, 3, 0)


@pytest.mark.parametrize("generator", [spi_controller, spi_target])
def test_bad_mode_is_rejected(generator) -> None:
    with pytest.raises(ValueError, match="mode must be"):
        generator(0, 1, 2, 3, 4)


def test_checked_in_controller_example_matches_the_generator() -> None:
    text = (EXAMPLES / "spi_controller_mode0.asm").read_text(encoding="utf-8")
    assert text == spi_controller(SCLK, CS, MOSI, MISO, 0)


def test_checked_in_target_example_matches_the_generator() -> None:
    text = (EXAMPLES / "spi_target_mode0.asm").read_text(encoding="utf-8")
    assert text == spi_target(SCLK, CS, MOSI, MISO, 0)
