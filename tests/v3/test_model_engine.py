"""Engine semantics: the fetch contract, the cursor, stalls and faults."""
import pytest

from protocol_emulator.v3 import isa, model


def run(source: str, cycles: int = 200) -> model.Engine:
    """Assemble, load, start, and run an engine for a fixed number of cycles."""
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble(source))
    base = model.Timebase()
    engine.start(base.now)
    for _ in range(cycles):
        engine.step(base.now)
        for cell in engine.pins:
            cell.tick(base.now)
        base.tick()
    return engine


def test_straight_line_code_retires_one_instruction_per_cycle() -> None:
    engine = run("LI reg=0 value=1\nLI reg=1 value=2\nLI reg=2 value=3\nHALT\n", cycles=6)
    assert engine.regs[0] == 1 and engine.regs[1] == 2 and engine.regs[2] == 3
    assert engine.retired == 4, "three loads plus the halt, one per cycle"


def test_a_taken_branch_costs_exactly_two_cycles() -> None:
    engine = run("JMP target=2\nFAIL code=1\nHALT\n", cycles=4)
    assert engine.halted
    assert engine.errors == 0
    assert engine.cycles_to_halt == 3, "one cycle for JMP, one bubble, one for HALT"


def test_an_untaken_branch_costs_one_cycle() -> None:
    source = "BRC target=3 condition=timeout\nLI reg=0 value=7\nHALT\nFAIL code=1\n"
    engine = run(source, cycles=6)
    assert engine.regs[0] == 7
    assert engine.cycles_to_halt == 3


def test_advance_moves_the_cursor_without_stalling() -> None:
    engine = run("ADVANCE ticks=1000\nHALT\n", cycles=3)
    assert engine.cursor == 1000
    assert engine.cycles_to_halt == 2, "ADVANCE never stalls"


def test_the_cursor_starts_at_now() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("HALT\n"))
    engine.start(now=12345)
    assert engine.cursor == 12345


def test_a_commit_schedules_at_cursor_plus_offset() -> None:
    engine = run("ADVANCE ticks=100\nOUT pin=0 level=1 offset=5\nHALT\n", cycles=4)
    assert engine.pins[0].pending == model.Pending(due=105, value=1, oe=1)


def test_a_commit_to_an_occupied_slot_stalls_the_engine() -> None:
    source = (
        "ADVANCE ticks=500\n"
        "OUT pin=0 level=1 offset=0\n"
        "OUT pin=0 level=0 offset=1\n"
        "HALT\n"
    )
    engine = run(source, cycles=10)
    # The second commit cannot be accepted while the first is pending, so the
    # engine is still stalled ten cycles in, 490 ticks short of the deadline.
    assert engine.stalled
    assert not engine.halted


def test_a_commit_to_an_unowned_pin_is_a_fault() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0x01)
    engine.load(isa.assemble("OUT pin=3 level=1 offset=0\nHALT\n"))
    engine.start(0)
    engine.step(0)
    assert engine.errors & model.E_OWNERSHIP


def test_an_unwritten_word_faults_instead_of_executing() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.memory.write(0, isa.encode("JMP", target=200))
    with pytest.raises(model.NotLoaded):
        engine.start(0)


def test_start_is_refused_until_every_word_is_written() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    assert engine.memory.valid is False
    engine.load(isa.assemble("HALT\n"))
    assert engine.memory.valid is True


def test_call_and_ret_nest_twice() -> None:
    source = (
        "CALL target=4\n"
        "HALT\n"
        "LI reg=2 value=3\n"
        "RET\n"
        "LI reg=0 value=1\n"
        "CALL target=2\n"
        "LI reg=1 value=2\n"
        "RET\n"
    )
    engine = run(source, cycles=30)
    assert (engine.regs[0], engine.regs[1], engine.regs[2]) == (1, 2, 3)
    assert engine.halted and engine.errors == 0


def test_a_third_nested_call_faults() -> None:
    source = "CALL target=2\nHALT\nCALL target=4\nRET\nCALL target=6\nRET\nRET\n"
    engine = run(source, cycles=30)
    assert engine.errors & model.E_STACK


def test_ret_with_an_empty_stack_faults() -> None:
    engine = run("RET\n", cycles=5)
    assert engine.errors & model.E_STACK


def test_djnz_loops_the_expected_number_of_times() -> None:
    source = "LI reg=0 value=3\nloop:\nLI reg=1 value=9\nDJNZ reg=0 target=loop\nHALT\n"
    engine = run(source, cycles=40)
    assert engine.regs[0] == 0
    assert engine.halted


def test_fail_sets_a_firmware_error_and_halts() -> None:
    engine = run("FAIL code=42\n", cycles=5)
    assert engine.errors & model.E_FIRMWARE
    assert engine.fault_code == 42
    assert engine.halted


def test_an_illegal_word_faults() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("HALT\n"))
    engine.memory.write(0, 0xFFFFFFFF)
    engine.start(0)
    engine.step(0)
    engine.step(1)
    assert engine.errors & model.E_ILLEGAL


def test_drive_writes_only_owned_pins() -> None:
    pins = [model.PinCell() for _ in range(8)]
    # Pre-existing state on unowned pins, as if the other engine set them.
    pins[4].pending = model.Pending(due=200, value=1, oe=1)
    pins[5].value, pins[5].oe = 1, 1
    engine = model.Engine(pins=pins, owner=0x0F)
    engine.load(isa.assemble("ADVANCE ticks=100\nDRIVE value=0x0F oe=0x0F offset=5\nHALT\n"))
    engine.start(0)
    engine.step(0)  # ADVANCE -> cursor 100
    engine.step(1)  # DRIVE, oe names only owned pins 0-3
    assert engine.errors == 0
    for i in range(4):
        assert pins[i].pending == model.Pending(due=105, value=1, oe=1)
    # Unowned pins are untouched: slot, value and enable unchanged.
    assert pins[4].pending == model.Pending(due=200, value=1, oe=1)
    assert (pins[5].value, pins[5].oe) == (1, 1)
    assert pins[6].pending is None and pins[7].pending is None


def test_drive_enabling_an_unowned_pin_faults_and_schedules_nothing() -> None:
    pins = [model.PinCell() for _ in range(8)]
    engine = model.Engine(pins=pins, owner=0x0F)
    # oe bit 4 names pin 4, which this engine does not own.
    engine.load(isa.assemble("DRIVE value=0xFF oe=0x10 offset=0\nHALT\n"))
    engine.start(0)
    engine.step(0)
    assert engine.errors & model.E_OWNERSHIP
    assert engine.halted
    # Nothing is scheduled or applied on any pin, owned or not.
    assert all(cell.pending is None for cell in pins)
    assert all((cell.value, cell.oe) == (0, 0) for cell in pins)


def test_drive_does_not_stall_on_a_busy_unowned_pin() -> None:
    pins = [model.PinCell() for _ in range(8)]
    pins[4].pending = model.Pending(due=5000, value=1, oe=1)  # busy, unowned
    engine = model.Engine(pins=pins, owner=0x0F)
    engine.load(isa.assemble("ADVANCE ticks=100\nDRIVE value=0x01 oe=0x01 offset=5\nHALT\n"))
    engine.start(0)
    engine.step(0)  # ADVANCE -> cursor 100
    engine.step(1)  # DRIVE must not stall on pin 4's occupied slot
    assert not engine.stalled
    assert engine.errors == 0
    assert pins[0].pending == model.Pending(due=105, value=1, oe=1)
    engine.step(2)  # HALT
    assert engine.halted


def test_faults_halt_the_engine_without_retiring() -> None:
    owned = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0x01)
    owned.load(isa.assemble("OUT pin=3 level=1 offset=0\nHALT\n"))
    owned.start(0)
    owned.step(0)
    assert owned.errors & model.E_OWNERSHIP
    assert owned.halted
    assert owned.retired == 0

    empty = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    empty.load(isa.assemble("RET\n"))
    empty.start(0)
    empty.step(0)
    assert empty.errors & model.E_STACK
    assert empty.halted
    assert empty.retired == 0


def test_add_sets_carry_and_zero() -> None:
    source = (
        "LI reg=0 value=0xFFFFFF\n"
        "LI reg=1 value=1\n"
        "ADD dst=0 src=1\n"  # wraps to 0, carry out, zero result
        "BRC target=skip condition=carry\n"
        "FAIL code=1\n"
        "skip:\n"
        "BRC target=done condition=zero\n"
        "FAIL code=2\n"
        "done:\n"
        "HALT\n"
    )
    engine = run(source, cycles=40)
    assert engine.halted and engine.errors == 0
    assert engine.regs[0] == 0


def test_add_without_carry_does_not_branch() -> None:
    source = (
        "LI reg=0 value=1\n"
        "LI reg=1 value=2\n"
        "ADD dst=0 src=1\n"  # 3: no carry, non-zero
        "BRC target=bad condition=carry\n"  # not taken
        "LI reg=2 value=7\n"
        "HALT\n"
        "bad:\n"
        "FAIL code=1\n"
    )
    engine = run(source, cycles=20)
    assert engine.halted and engine.errors == 0
    assert engine.regs[0] == 3 and engine.regs[2] == 7


def test_sub_sets_the_borrow_in_carry() -> None:
    source = (
        "LI reg=0 value=1\n"
        "LI reg=1 value=2\n"
        "SUB dst=0 src=1\n"  # 1 - 2 borrows, result wraps to 0xFFFFFF
        "BRC target=skip condition=carry\n"
        "FAIL code=1\n"
        "skip:\n"
        "HALT\n"
    )
    engine = run(source, cycles=20)
    assert engine.halted and engine.errors == 0
    assert engine.regs[0] == 0xFFFFFF


def test_shl_carry_is_the_bit_shifted_past_the_top() -> None:
    source = (
        "LI reg=0 value=0x800000\n"  # bit 23 set
        "SHL dst=0 amount=1\n"  # shifts bit 23 out, result 0
        "BRC target=skip condition=carry\n"
        "FAIL code=1\n"
        "skip:\n"
        "HALT\n"
    )
    engine = run(source, cycles=20)
    assert engine.halted and engine.errors == 0
    assert engine.regs[0] == 0


def test_shr_carry_is_the_last_bit_shifted_out() -> None:
    source = (
        "LI reg=0 value=1\n"
        "SHR dst=0 amount=1\n"  # shifts bit 0 out, result 0
        "BRC target=skip condition=carry\n"
        "FAIL code=1\n"
        "skip:\n"
        "HALT\n"
    )
    engine = run(source, cycles=20)
    assert engine.halted and engine.errors == 0
    assert engine.regs[0] == 0


def test_waittime_stalls_until_now_reaches_the_cursor() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("ADVANCE ticks=5\nWAITTIME\nHALT\n"))
    engine.start(0)
    engine.step(0)  # ADVANCE -> cursor 5
    assert engine.cursor == 5
    for now in range(1, 5):  # now 1..4: signed_distance > 0, still stalled
        engine.step(now)
        assert engine.stalled and not engine.halted
    engine.step(5)  # signed_distance == 0: WAITTIME proceeds
    assert not engine.stalled
    engine.step(6)  # HALT
    assert engine.halted


def test_waittime_proceeds_across_the_timebase_wrap() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("ADVANCE ticks=5\nWAITTIME\nHALT\n"))
    start_now = model.TIMEBASE_MASK - 2  # 0xFFFFFD
    engine.start(start_now)
    engine.step(start_now)  # ADVANCE -> cursor wraps to 2
    assert engine.cursor == 2
    for now in (0xFFFFFE, 0xFFFFFF, 0x000000, 0x000001):
        engine.step(now)
        assert engine.stalled and not engine.halted
    engine.step(0x000002)  # signed_distance(2, 2) == 0: proceeds
    assert not engine.stalled
    engine.step(0x000003)  # HALT
    assert engine.halted
