"""Input path and input instructions: synchronizer, filter, sampling and waits."""
from collections.abc import Callable

from protocol_emulator.v3 import isa, model


def new_engine(source: str, owner: int = 0xFF) -> model.Engine:
    """Assemble ``source`` onto a fresh engine owning ``owner``'s pins."""
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=owner)
    engine.load(isa.assemble(source))
    return engine


def observe_byte(engine: model.Engine, value: int, now: int) -> None:
    """Feed ``value`` as the engine-visible level of all eight pins at ``now``."""
    for index in range(8):
        engine.pins[index].observe((value >> index) & 1, now)


def run_until_halt(
    engine: model.Engine,
    byte_at: Callable[[int], int],
    max_cycles: int = 200,
    start_now: int = 0,
) -> int:
    """Start, then step with ``byte_at(now)`` observed each cycle until halt."""
    base = model.Timebase()
    base.now = start_now
    engine.start(base.now)
    cycles = 0
    while engine.running and cycles < max_cycles:
        observe_byte(engine, byte_at(base.now), base.now)
        engine.step(base.now)
        for cell in engine.pins:
            cell.tick(base.now)
        base.tick()
        cycles += 1
    return cycles


# -- input path building blocks -----------------------------------------


def test_two_flop_synchronizer_delays_by_two_cycles() -> None:
    sync = model.Synchronizer()
    outputs = [sync.tick(0x01) & 1 for _ in range(5)]
    assert outputs == [0, 0, 1, 1, 1], "a steady input reaches the output after two flops"


def test_filter_qualifies_a_change_after_three_agreeing_samples() -> None:
    filt = model.InputFilter()
    outputs = [filt.tick(0x01) & 1 for _ in range(5)]
    assert outputs == [0, 0, 0, 1, 1]


def test_filter_rejects_a_short_glitch() -> None:
    filt = model.InputFilter()
    outputs = [filt.tick(bit) & 1 for bit in (0, 1, 1, 0, 0, 0)]
    assert all(level == 0 for level in outputs), "a two-cycle glitch never qualifies"


def test_synchronizer_then_filter_latency_is_five_cycles() -> None:
    sync = model.Synchronizer()
    filt = model.InputFilter()
    outputs = [filt.tick(sync.tick(0x01)) & 1 for _ in range(8)]
    assert outputs == [0, 0, 0, 0, 0, 1, 1, 1], "two sync cycles plus three filter cycles"


def test_filter_is_independent_per_pin() -> None:
    filt = model.InputFilter()
    out = 0
    for _ in range(4):
        out = filt.tick(0b0000_0101)
    assert out == 0b0000_0101, "pins 0 and 2 qualify independently of the rest"


# -- SAMPLE / IN / EDGETIME / MOVIS / CLEARIS ---------------------------


def test_sample_reads_all_eight_filtered_levels_into_a_register() -> None:
    engine = new_engine("SAMPLE reg=1\nHALT\n")
    engine.start(0)
    observe_byte(engine, 0b1011_0101, 0)
    engine.step(0)
    assert engine.regs[1] == 0b1011_0101
    assert engine.regs[1] <= 0xFF, "the upper sixteen bits are zero"


def test_in_builds_the_isr_msb_first() -> None:
    engine = new_engine("IN pin=0 msb_first=1\n" * 8 + "HALT\n")
    engine.start(0)
    bits = [1, 0, 1, 1, 0, 0, 1, 0]
    for cycle, bit in enumerate(bits):
        observe_byte(engine, bit, cycle)
        engine.step(cycle)
    assert engine.isr == 0b1011_0010


def test_in_builds_the_isr_lsb_first() -> None:
    engine = new_engine("IN pin=0 msb_first=0\n" * 8 + "HALT\n")
    engine.start(0)
    bits = [1, 0, 1, 1, 0, 0, 1, 0]
    for cycle, bit in enumerate(bits):
        observe_byte(engine, bit, cycle)
        engine.step(cycle)
    expected = 0
    for bit in bits:
        expected = (expected >> 1) | (bit << 7)
    assert engine.isr == expected


def test_edgetime_reads_the_last_filtered_edge_timestamp() -> None:
    engine = new_engine("EDGETIME pin=2 reg=0\nHALT\n")
    engine.start(0)
    engine.pins[2].observe(0, 0)
    engine.pins[2].observe(1, 41)
    engine.step(100)
    assert engine.regs[0] == 41


def test_edgetime_is_stable_across_the_timebase_wrap() -> None:
    engine = new_engine("EDGETIME pin=0 reg=0\nHALT\n")
    engine.start(0)
    edge = model.TIMEBASE_MASK - 3
    engine.pins[0].observe(0, edge - 1)
    engine.pins[0].observe(1, edge)
    engine.step(5)
    assert engine.regs[0] == edge


def test_movis_copies_the_isr_and_clearis_zeroes_it() -> None:
    engine = new_engine("MOVIS reg=3\nCLEARIS\nMOVIS reg=2\nHALT\n")
    engine.start(0)
    engine.isr = 0xA5
    engine.step(0)
    assert engine.regs[3] == 0xA5
    engine.step(1)
    assert engine.isr == 0
    engine.step(2)
    assert engine.regs[2] == 0


# -- WAITEDGE / WAITFOR -------------------------------------------------


def test_waitedge_rising_retires_on_the_matching_edge() -> None:
    engine = new_engine("ADVANCE ticks=50\nWAITEDGE pin=0 polarity=rising\nHALT\n")
    run_until_halt(engine, lambda now: 0x01 if now >= 10 else 0x00, start_now=1)
    assert engine.halted and engine.errors == 0
    assert engine._flag_timeout is False
    assert engine._flag_interrupted is False


def test_waitedge_falling_ignores_a_rising_edge() -> None:
    engine = new_engine("ADVANCE ticks=50\nWAITEDGE pin=0 polarity=falling\nHALT\n")

    def byte_at(now: int) -> int:
        if now < 5:
            return 0x00
        if now < 20:
            return 0x01
        return 0x00

    run_until_halt(engine, byte_at, start_now=1)
    assert engine.halted and engine._flag_timeout is False


def test_waitedge_either_counts_an_edge_on_the_entry_cycle() -> None:
    engine = new_engine("ADVANCE ticks=50\nWAITEDGE pin=0 polarity=either\nHALT\n")
    cycles = run_until_halt(engine, lambda now: 0x01 if now >= 2 else 0x00, start_now=1)
    assert engine.halted and engine._flag_timeout is False
    assert cycles == 3, "ADVANCE, an entry-cycle match, then HALT, with no stall"


def test_waitedge_times_out_at_the_cursor_without_an_edge() -> None:
    engine = new_engine("ADVANCE ticks=5\nWAITEDGE pin=0 polarity=rising\nHALT\n")
    run_until_halt(engine, lambda now: 0x00, start_now=1)
    assert engine.halted
    assert engine._flag_timeout is True
    assert engine._flag_interrupted is False


def test_waitedge_ignores_a_steady_level_and_an_edge_before_the_baseline() -> None:
    engine = new_engine("ADVANCE ticks=5\nWAITEDGE pin=0 polarity=rising\nHALT\n")
    # Pin high from the start: its only edge is before the wait baseline, so the
    # wait never reacts to the steady level and times out instead.
    run_until_halt(engine, lambda now: 0x01, start_now=1)
    assert engine._flag_timeout is True


def test_waitfor_retires_when_the_level_matches() -> None:
    engine = new_engine("ADVANCE ticks=50\nWAITFOR pin=1 level=1\nHALT\n")
    run_until_halt(engine, lambda now: 0x02 if now >= 8 else 0x00, start_now=1)
    assert engine.halted and engine._flag_timeout is False


def test_waitfor_times_out_when_the_level_never_matches() -> None:
    engine = new_engine("ADVANCE ticks=5\nWAITFOR pin=1 level=1\nHALT\n")
    run_until_halt(engine, lambda now: 0x00, start_now=1)
    assert engine._flag_timeout is True
