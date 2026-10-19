"""Encode and decode are exact inverses, and illegal words are rejected."""
import pytest

from protocol_emulator.v3 import isa, opcodes


def test_nop_is_all_zero() -> None:
    assert isa.encode("NOP") == 0


def test_opcode_lands_in_the_top_six_bits() -> None:
    word = isa.encode("HALT")
    assert word >> 26 == opcodes.OPS["HALT"].code


def test_li_packs_register_then_value() -> None:
    word = isa.encode("LI", reg=2, value=0xABCDEF)
    assert word >> 26 == 0x08
    assert word & 0x3 == 2
    assert (word >> 2) & 0xFFFFFF == 0xABCDEF


def test_every_instruction_round_trips_at_field_maxima() -> None:
    for op in opcodes.OPS.values():
        operands = {field.name: (1 << field.width) - 1 for field in op.fields}
        if op.name == "BRC":
            operands["condition"] = max(opcodes.CONDITIONS.values())
        if op.name == "WAITEDGE":
            operands["polarity"] = max(opcodes.POLARITIES.values())
        word = isa.encode(op.name, **operands)
        back = isa.decode(word)
        assert back.name == op.name
        assert back.operands == operands


def test_every_instruction_round_trips_at_zero() -> None:
    for op in opcodes.OPS.values():
        operands = {field.name: 0 for field in op.fields}
        assert isa.decode(isa.encode(op.name, **operands)).operands == operands


def test_an_out_of_range_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="out of range"):
        isa.encode("OUT", pin=8, level=0, offset=0)


def test_a_missing_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="expects"):
        isa.encode("OUT", pin=0)


def test_an_unexpected_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="expects"):
        isa.encode("NOP", pin=0)


def test_an_unused_opcode_is_illegal() -> None:
    unused = next(code for code in range(64) if code not in opcodes.BY_CODE)
    with pytest.raises(isa.IllegalInstruction, match="unused opcode"):
        isa.decode(unused << 26)


def test_a_set_reserved_bit_is_illegal() -> None:
    # NOP defines no operand fields, so any set operand bit is reserved.
    with pytest.raises(isa.IllegalInstruction, match="reserved"):
        isa.decode(1)


def test_a_reserved_condition_is_illegal() -> None:
    bad = max(opcodes.CONDITIONS.values()) + 1
    word = (opcodes.OPS["BRC"].code << 26) | (bad << 8)
    with pytest.raises(isa.IllegalInstruction, match="condition"):
        isa.decode(word)


def test_a_word_wider_than_32_bits_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="32"):
        isa.decode(1 << 32)
