"""The V3 opcode table. Single source of truth.

The assembler, the cycle-accurate model and the generated Verilog legality
check all derive from this table. Changing an encoding here changes all three;
``tests/v3/test_opcode_header.py`` fails if the generated Verilog drifts.

Word layout::

    31          26 25                             0
    +-------------+-------------------------------+
    |   opcode    |            operand            |
    +-------------+-------------------------------+

Operand fields are packed from bit 0 upward in declaration order. Bits outside
a declared field must be zero; a word with a set reserved bit is illegal.

Timing classes:

``single``
    Retires in one cycle. The prefetched next word is used.
``branch``
    May redirect the fetch. A taken branch costs exactly two cycles.
``stall``
    May hold the engine for an unbounded or input-dependent number of cycles.
"""
from typing import NamedTuple


class Field(NamedTuple):
    """One operand field, packed at ``shift`` and ``width`` bits wide."""

    name: str
    width: int
    shift: int


class Op(NamedTuple):
    """One instruction: mnemonic, 6-bit opcode, operand layout, timing class."""

    name: str
    code: int
    fields: tuple[Field, ...]
    timing: str


def _op(name: str, code: int, timing: str, *fields: tuple[str, int]) -> Op:
    packed: list[Field] = []
    shift = 0
    for field_name, width in fields:
        packed.append(Field(field_name, width, shift))
        shift += width
    if shift > 26:
        raise ValueError(f"{name} operand fields need {shift} bits; only 26 are available")
    return Op(name, code, tuple(packed), timing)


#: Branch conditions for ``BRC``.
CONDITIONS = {
    "timeout": 0,
    "interrupted": 1,
    "missed": 2,
    "tx_empty": 3,
    "rx_full": 4,
    "pull_empty": 5,
    "event0": 6,
    "event1": 7,
    "zero": 8,
    "carry": 9,
}

#: Edge polarities for ``WAITEDGE``.
POLARITIES = {"rising": 0, "falling": 1, "either": 2}

_TABLE = [
    # --- control -------------------------------------------------------
    _op("NOP", 0x00, "single"),
    _op("HALT", 0x01, "single"),
    _op("JMP", 0x02, "branch", ("target", 8)),
    _op("BRC", 0x03, "branch", ("target", 8), ("condition", 4)),
    _op("DJNZ", 0x04, "branch", ("target", 8), ("reg", 2)),
    _op("CALL", 0x05, "branch", ("target", 8)),
    _op("RET", 0x06, "branch"),
    _op("FAIL", 0x07, "single", ("code", 8)),
    # --- registers -----------------------------------------------------
    _op("LI", 0x08, "single", ("reg", 2), ("value", 24)),
    _op("MOV", 0x09, "single", ("dst", 2), ("src", 2)),
    _op("ADD", 0x0A, "single", ("dst", 2), ("src", 2)),
    _op("SUB", 0x0B, "single", ("dst", 2), ("src", 2)),
    _op("AND", 0x0C, "single", ("dst", 2), ("src", 2)),
    _op("OR", 0x0D, "single", ("dst", 2), ("src", 2)),
    _op("XOR", 0x0E, "single", ("dst", 2), ("src", 2)),
    _op("SHL", 0x0F, "single", ("dst", 2), ("amount", 5)),
    _op("SHR", 0x10, "single", ("dst", 2), ("amount", 5)),
    _op("BREQ", 0x11, "branch", ("target", 8), ("reg", 2), ("value", 8)),
    # --- time ----------------------------------------------------------
    _op("ADVANCE", 0x14, "single", ("ticks", 24)),
    _op("ADVANCER", 0x15, "single", ("reg", 2)),
    _op("CURSORSET", 0x16, "single", ("reg", 2)),
    _op("CURSORGET", 0x17, "single", ("reg", 2)),
    _op("NOW", 0x18, "single", ("reg", 2)),
    _op("WAITTIME", 0x19, "stall"),
    # --- pin commits ---------------------------------------------------
    _op("SET", 0x1C, "stall", ("mask", 8), ("value", 8), ("offset", 10)),
    _op("OE", 0x1D, "stall", ("mask", 8), ("oe", 8), ("offset", 10)),
    _op("DRIVE", 0x1E, "stall", ("value", 8), ("oe", 8), ("offset", 10)),
    _op("OUT", 0x1F, "stall", ("pin", 3), ("level", 1), ("offset", 10)),
    # --- inputs --------------------------------------------------------
    _op("SAMPLE", 0x20, "single", ("reg", 2)),
    _op("IN", 0x21, "single", ("pin", 3), ("msb_first", 1)),
    _op("EDGETIME", 0x22, "single", ("pin", 3), ("reg", 2)),
    _op("WAITEDGE", 0x23, "stall", ("pin", 3), ("polarity", 2)),
    _op("WAITFOR", 0x24, "stall", ("pin", 3), ("level", 1)),
    _op("MOVIS", 0x25, "single", ("reg", 2)),
    _op("CLEARIS", 0x26, "single"),
    # --- queues --------------------------------------------------------
    _op("PULL", 0x28, "stall", ("reg", 2)),
    _op("PULLNB", 0x29, "single", ("reg", 2)),
    _op("RX", 0x2A, "single", ("meta", 8)),
    # --- events --------------------------------------------------------
    _op("MARK", 0x2C, "single", ("code", 8)),
    _op("WAITEVENT", 0x2D, "stall", ("mask", 8)),
    _op("CLEAREVENT", 0x2E, "single", ("mask", 8)),
    _op("CLEARMISS", 0x2F, "single", ("mask", 8)),
    # --- CRC -----------------------------------------------------------
    _op("CRCCFG", 0x30, "single", ("poly", 16), ("msb_first", 1)),
    _op("CRCLOAD", 0x31, "single", ("reg", 2)),
    _op("CRCBIT", 0x32, "single", ("reg", 2)),
    _op("CRCGET", 0x33, "single", ("reg", 2)),
]

#: Mnemonic to :class:`Op`.
OPS: dict[str, Op] = {op.name: op for op in _TABLE}

#: Opcode number to :class:`Op`.
BY_CODE: dict[int, Op] = {op.code: op for op in _TABLE}


def operand_mask(op: Op) -> int:
    """Return the mask of operand bits ``op`` defines.

    Bits set outside this mask make the instruction word illegal.
    """
    mask = 0
    for field in op.fields:
        mask |= ((1 << field.width) - 1) << field.shift
    return mask
