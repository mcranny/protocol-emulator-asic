"""V3 instruction encoding, decoding and assembly.

All encoding behaviour is derived from :mod:`protocol_emulator.v3.opcodes`.
There are no per-instruction special cases in this module: adding an
instruction means adding a row to the table.
"""
import re
from typing import NamedTuple

from . import PROGRAM_WORDS, opcodes
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


class AssemblyError(ValueError):
    """A source program could not be assembled."""


class Image(NamedTuple):
    """An assembled program.

    :ivar words: exactly :data:`protocol_emulator.v3.PROGRAM_WORDS` words, with
        unwritten addresses filled with ``HALT``.
    :ivar labels: label name to instruction address.
    :ivar used: the number of addresses written by the source.
    """

    words: tuple[int, ...]
    labels: dict[str, int]
    used: int


_LABEL_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _check_label(name: str, line: int) -> None:
    if not name or _LABEL_RE.fullmatch(name) is None:
        raise AssemblyError(f"line {line}: invalid label {name!r}")
    if name in CONDITIONS or name in POLARITIES:
        raise AssemblyError(f"line {line}: invalid label {name!r}")


def _tokenize(source: str) -> list[tuple[int, str]]:
    """Return ``(line_number, text)`` for every non-blank, non-comment line."""
    lines = []
    for number, raw in enumerate(source.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if text:
            lines.append((number, text))
    return lines


def _resolve(token: str, labels: dict[str, int], field: str, line: int) -> int:
    """Resolve one operand token to an integer."""
    if field == "condition" and token in CONDITIONS:
        return CONDITIONS[token]
    if field == "polarity" and token in POLARITIES:
        return POLARITIES[token]
    try:
        return int(token, 0)
    except ValueError:
        pass
    if token in labels:
        return labels[token]
    raise AssemblyError(f"line {line}: undefined label or value {token!r}")


def assemble(source: str) -> Image:
    """Assemble ``source`` into a full program image.

    Two passes: the first records label addresses, the second encodes. Both
    honour ``.org``. Addresses not written by the source are filled with
    ``HALT`` so the loader can write all 256 words and the validity bitmap can
    be fully set.

    :raises AssemblyError: on a syntax error, a duplicate or undefined label,
        two instructions at one address, or a program larger than 256 words.
    """
    lines = _tokenize(source)

    labels: dict[str, int] = {}
    address = 0
    for number, text in lines:
        if text.endswith(":"):
            name = text[:-1].strip()
            _check_label(name, number)
            if name in labels:
                raise AssemblyError(f"line {number}: duplicate label {name!r}")
            labels[name] = address
            continue
        if text.startswith(".org"):
            address = _org_address(text, number)
            continue
        if address >= PROGRAM_WORDS:
            raise AssemblyError(
                f"line {number}: program does not fit in {PROGRAM_WORDS} words"
            )
        address += 1

    words: list[int | None] = [None] * PROGRAM_WORDS
    address = 0
    for number, text in lines:
        if text.endswith(":"):
            continue
        if text.startswith(".org"):
            address = _org_address(text, number)
            continue
        if address >= PROGRAM_WORDS:
            raise AssemblyError(
                f"line {number}: program does not fit in {PROGRAM_WORDS} words"
            )
        if words[address] is not None:
            raise AssemblyError(f"line {number}: address {address} is already occupied")
        words[address] = _encode_line(text, labels, number)
        address += 1

    used = sum(1 for word in words if word is not None)
    halt = encode("HALT")
    return Image(tuple(halt if word is None else word for word in words), labels, used)


def _org_address(text: str, line: int) -> int:
    parts = text.split()
    if len(parts) != 2:
        raise AssemblyError(f"line {line}: .org takes exactly one address")
    try:
        address = int(parts[1], 0)
    except ValueError:
        raise AssemblyError(f"line {line}: .org address {parts[1]!r} is not a number") from None
    if not 0 <= address < PROGRAM_WORDS:
        raise AssemblyError(
            f"line {line}: program does not fit, .org {address} is outside 0..{PROGRAM_WORDS - 1}"
        )
    return address


def _encode_line(text: str, labels: dict[str, int], line: int) -> int:
    parts = text.replace(",", " ").split()
    mnemonic, assignments = parts[0], parts[1:]
    op = opcodes.OPS.get(mnemonic.upper())
    if op is None:
        raise AssemblyError(f"line {line}: unknown instruction {mnemonic!r}")
    allowed = {field.name for field in op.fields}
    operands: dict[str, int] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise AssemblyError(
                f"line {line}: operand {assignment!r} is not in name=value form"
            )
        field, token = assignment.split("=", 1)
        if field in operands:
            raise AssemblyError(f"line {line}: duplicate operand {field!r}")
        if allowed is not None and field not in allowed:
            raise AssemblyError(f"line {line}: unknown operand {field!r}")
        operands[field] = _resolve(token, labels, field, line)
    try:
        return encode(mnemonic, **operands)
    except IllegalInstruction as error:
        raise AssemblyError(f"line {line}: {error}") from None
