"""V3 instruction encoding, decoding and assembly.

All encoding behaviour is derived from :mod:`protocol_emulator.v3.opcodes`.
There are no per-instruction special cases in this module: adding an
instruction means adding a row to the table.
"""
from typing import NamedTuple

from . import opcodes
from .opcodes import CONDITIONS, POLARITIES

WORD_BITS = 32
OPCODE_SHIFT = 26
OPERAND_BITS = OPCODE_SHIFT


class IllegalInstruction(ValueError):
    """An instruction cannot be encoded, or a word is not a legal instruction."""


class Instruction(NamedTuple):
    """A decoded instruction: mnemonic plus named operand values."""

    name: str
    operands: dict[str, int]


def encode(name: str, **operands: int) -> int:
    """Encode ``name`` with the given named operands into a 32-bit word.

    :raises IllegalInstruction: on an unknown mnemonic, a wrong operand set, or
        an operand too wide for its field.
    """
    key = name.upper()
    op = opcodes.OPS.get(key)
    if op is None:
        raise IllegalInstruction(f"unknown instruction {name!r}")
    expected = {field.name for field in op.fields}
    if set(operands) != expected:
        raise IllegalInstruction(
            f"{key} expects operands {sorted(expected)}, got {sorted(operands)}"
        )
    word = op.code << OPCODE_SHIFT
    for field in op.fields:
        value = operands[field.name]
        limit = (1 << field.width) - 1
        if not 0 <= value <= limit:
            raise IllegalInstruction(
                f"{key}.{field.name}={value} is out of range 0..{limit}"
            )
        word |= value << field.shift
    return word


def decode(word: int) -> Instruction:
    """Decode a 32-bit word.

    :raises IllegalInstruction: if the word is too wide, uses an unused opcode,
        sets a reserved operand bit, or carries a reserved enumerated value.
    """
    if not 0 <= word < (1 << WORD_BITS):
        raise IllegalInstruction(f"instruction word must fit in {WORD_BITS} bits")
    code = word >> OPCODE_SHIFT
    operand = word & ((1 << OPERAND_BITS) - 1)
    op = opcodes.BY_CODE.get(code)
    if op is None:
        raise IllegalInstruction(f"unused opcode 0x{code:02X}")
    if operand & ~opcodes.operand_mask(op):
        raise IllegalInstruction(f"{op.name} has a reserved operand bit set")
    values = {
        field.name: (operand >> field.shift) & ((1 << field.width) - 1)
        for field in op.fields
    }
    if op.name == "BRC" and values["condition"] not in CONDITIONS.values():
        raise IllegalInstruction(f"reserved condition {values['condition']}")
    if op.name == "WAITEDGE" and values["polarity"] not in POLARITIES.values():
        raise IllegalInstruction(f"reserved polarity {values['polarity']}")
    return Instruction(op.name, values)


def is_legal(word: int) -> bool:
    """Return whether ``word`` decodes. Mirrors ``legal()`` in the RTL."""
    try:
        decode(word)
    except IllegalInstruction:
        return False
    return True
