"""24-bit V2 candidate ISA, retaining the V1 instruction encodings.

Extension opcode D has a four-bit subopcode and sixteen operand bits. This
module is deliberately separate from the released V1 codec.
"""
from dataclasses import dataclass
import re

from .. import isa as v1


@dataclass(frozen=True)
class Instruction:
    name: str
    operands: tuple[int, ...]


# Fields are packed from least significant to most significant in this order.
# Branch targets are instruction addresses, never byte offsets.
EXTENSIONS = {
    "IN": (0, (3, 1)),             # pin, MSB-first (8-bit accumulator)
    "RX": (1, (8,)),              # publish ISR with metadata
    "CLEARIS": (2, ()),
    "BRPIN": (3, (7, 3, 1)),      # target, pin, level
    "BREQ": (4, (7, 8, 1)),       # target, byte value, register
    "BRC": (5, (7, 4)),           # target, condition
    "DRIVE": (6, (8, 8)),         # output value, output enable
    "WAITFOR": (7, (3, 1)),       # pin, level; timeout from r1
    "MARK": (8, (8,)),
    "WAITEVENT": (9, (8,)),       # event mask; timeout from r1
    "MOVIS": (10, (1,)),          # ISR to register
    "OUT8": (11, (3,)),           # MSB-first byte output
    "PULLNB": (12, (1,)),         # nonblocking; leaves register on empty
    "CLEAREVENT": (13, (8,)),
    "FAIL": (14, (8,)),           # sticky firmware error and halt
    "BRDIFF": (15, (7, 3)),       # target, pin; observed != requested output
}
BY_SUBOP = {sub: (name, widths) for name, (sub, widths) in EXTENSIONS.items()}
CONDITIONS = {"tx_empty": 0, "rx_full": 1, "timeout": 2,
              "interrupted": 3, "event0": 4, "event1": 5,
              "pull_empty": 6}


def encode(name, *operands):
    name = name.upper()
    if name == "CALL":
        if len(operands) != 1:
            return _bad(name)
        return 0xE00000 | v1.bounded(operands[0], 127)
    if name in ("RET", "UNLINK"):
        if operands:
            return _bad(name)
        return 0xE10000 if name == "RET" else 0xE20000
    if name == "WAITBR":
        if len(operands) != 3:
            return _bad(name)
        target, pin, level = operands
        return 0xE30000 | v1.bounded(target, 127) | v1.bounded(pin, 7) << 7 | v1.bounded(level, 1) << 10
    if name == "JMP":
        return 0xB00000 | v1.bounded(operands[0], 127) if len(operands) == 1 else _bad(name)
    if name == "DJNZ":
        if len(operands) != 2:
            return _bad(name)
        return 0xA00000 | v1.bounded(operands[0], 1) << 16 | v1.bounded(operands[1], 127)
    if name not in EXTENSIONS:
        return v1.encode(name, *operands)
    sub, widths = EXTENSIONS[name]
    if len(operands) != len(widths):
        return _bad(name)
    word, shift = 0xD00000 | sub << 16, 0
    for operand, width in zip(operands, widths):
        word |= v1.bounded(operand, (1 << width) - 1) << shift
        shift += width
    return word


def _bad(name):
    raise ValueError(f"wrong operand count for {name}")


def decode(word):
    v1.bounded(word, 0xFFFFFF)
    op, args = word >> 20, word & 0xFFFFF
    if op == 14:
        if args < 128:
            return Instruction("CALL", (args,))
        if args == 0x10000:
            return Instruction("RET", ())
        if args == 0x20000:
            return Instruction("UNLINK", ())
        if args >> 16 == 3 and not args & 0xF800:
            return Instruction("WAITBR", (args & 127, args >> 7 & 7, args >> 10 & 1))
        raise ValueError("reserved flow-control encoding")
    if op == 10 and not args & ~0x1007F:
        return Instruction("DJNZ", (args >> 16, args & 127))
    if op == 11 and not args & ~127:
        return Instruction("JMP", (args,))
    if op != 13:
        insn = v1.decode(word)
        return Instruction(insn.name, insn.operands)
    sub = args >> 16
    if sub not in BY_SUBOP:
        raise ValueError("reserved V2 subopcode")
    name, widths = BY_SUBOP[sub]
    remaining = args & 65535
    operands = []
    for width in widths:
        operands.append(remaining & ((1 << width) - 1))
        remaining >>= width
    if remaining:
        raise ValueError("reserved V2 operand bits")
    if name == "BRC" and operands[1] not in CONDITIONS.values():
        raise ValueError("reserved condition")
    return Instruction(name, tuple(operands))


def assemble(source, capacity=128):
    if capacity not in (64, 128):
        raise ValueError("capacity must be 64 or 128")
    labels, lines = {}, []
    for number, raw in enumerate(source.splitlines(), 1):
        line = re.split(r"[#;]", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        if ":" in line:
            label, line = map(str.strip, line.split(":", 1))
            if not re.fullmatch(r"[A-Za-z_]\w*", label) or label in labels:
                raise ValueError(f"line {number}: invalid label")
            labels[label] = len(lines)
        if line:
            lines.append((number, line))
    if not 1 <= len(lines) <= capacity:
        raise ValueError(f"program has {len(lines)} words; capacity is {capacity}")
    words = []
    for number, line in lines:
        name, *rest = line.split(None, 1)
        tokens = rest[0].split(",") if rest else []
        args = []
        for index, token in enumerate(tokens):
            token = token.strip()
            target = (name.upper() in ("JMP", "BRPIN", "BREQ", "BRC", "BRDIFF", "CALL", "WAITBR") and index == 0 or
                      name.upper() == "DJNZ" and index == 1)
            args.append(labels[token] if target and token in labels else int(token, 0))
            if target and not 0 <= args[-1] < capacity:
                raise ValueError(f"line {number}: branch outside memory")
        word = encode(name, *args)
        decode(word)
        words.append(word)
    return words
