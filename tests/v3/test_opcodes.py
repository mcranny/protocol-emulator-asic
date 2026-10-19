"""The opcode table is internally consistent and fits the 32-bit word."""
import pytest

from protocol_emulator.v3 import opcodes


def test_every_opcode_fits_six_bits() -> None:
    for op in opcodes.OPS.values():
        assert 0 <= op.code < 64, op.name


def test_opcodes_are_unique() -> None:
    codes = [op.code for op in opcodes.OPS.values()]
    assert len(codes) == len(set(codes))


def test_operand_fields_fit_26_bits_and_do_not_overlap() -> None:
    for op in opcodes.OPS.values():
        used = 0
        for field in op.fields:
            span = ((1 << field.width) - 1) << field.shift
            assert span <= 0x3FFFFFF, f"{op.name}.{field.name} exceeds the operand field"
            assert not used & span, f"{op.name}.{field.name} overlaps another field"
            used |= span
        assert used == opcodes.operand_mask(op)


def test_by_code_round_trips() -> None:
    for op in opcodes.OPS.values():
        assert opcodes.BY_CODE[op.code] is op


def test_timing_classes_are_known() -> None:
    allowed = {"single", "branch", "stall"}
    for op in opcodes.OPS.values():
        assert op.timing in allowed, op.name


def test_required_instructions_are_present() -> None:
    required = {
        "NOP", "HALT", "JMP", "BRC", "DJNZ", "CALL", "RET", "FAIL",
        "LI", "MOV", "ADD", "SUB", "AND", "OR", "XOR", "SHL", "SHR", "BREQ",
        "ADVANCE", "ADVANCER", "CURSORSET", "CURSORGET", "NOW", "WAITTIME",
        "SET", "OE", "DRIVE", "OUT",
        "SAMPLE", "IN", "EDGETIME", "WAITEDGE", "WAITFOR", "MOVIS", "CLEARIS",
        "PULL", "PULLNB", "RX",
        "MARK", "WAITEVENT", "CLEAREVENT", "CLEARMISS",
        "CRCCFG", "CRCLOAD", "CRCBIT", "CRCGET",
    }
    assert required <= set(opcodes.OPS)


def test_commit_instructions_all_carry_a_ten_bit_offset() -> None:
    for name in ("SET", "OE", "DRIVE", "OUT"):
        offset = next(f for f in opcodes.OPS[name].fields if f.name == "offset")
        assert offset.width == 10
