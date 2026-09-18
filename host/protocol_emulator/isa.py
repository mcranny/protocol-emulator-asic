"""ISA v1 assembler and binary image validation."""
from dataclasses import dataclass
import re

NAMES = ("NOP", "SET", "OE", "LI", "PULL", "MOVOS", "OUT", "SAMPLE",
         "DELAY", "WAIT", "DJNZ", "JMP", "HALT")
MASKS = (0, 0xFFFF, 0xFFFF, 0x1FFFF, 0x10000, 0x10000, 0xF,
         0x100FF, 0xFFFF, 0xFFFFF, 0x1003F, 0x3F, 0)
COUNTS = (0, 2, 2, 2, 1, 1, 2, 2, 1, 3, 2, 1, 0)


@dataclass(frozen=True)
class Instruction:
    name: str
    operands: tuple[int, ...]


def bounded(value: int, maximum: int, minimum: int = 0) -> int:
    if not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"operand {value!r} outside {minimum}..{maximum}")
    return value


def encode(name: str, *args: int) -> int:
    try:
        op = NAMES.index(name.upper())
    except ValueError as exc:
        raise ValueError(f"unknown instruction {name!r}") from exc
    if len(args) != COUNTS[op]:
        raise ValueError(f"{name} expects {COUNTS[op]} operands")
    a = args
    operand = 0
    if op in (1, 2):
        operand = bounded(a[0], 255) << 8 | bounded(a[1], 255)
    elif op == 3:
        operand = bounded(a[0], 1) << 16 | bounded(a[1], 65535)
    elif op in (4, 5):
        operand = bounded(a[0], 1) << 16
    elif op == 6:
        operand = bounded(a[0], 7) | bounded(a[1], 1) << 3
    elif op == 7:
        operand = bounded(a[0], 1) << 16 | bounded(a[1], 255)
    elif op == 8:
        operand = bounded(a[0], 65535, 1)
    elif op == 9:
        operand = bounded(a[0], 7) | bounded(a[1], 1) << 3 | bounded(a[2], 65535, 1) << 4
    elif op == 10:
        operand = bounded(a[0], 1) << 16 | bounded(a[1], 63)
    elif op == 11:
        operand = bounded(a[0], 63)
    return op << 20 | operand


def decode(word: int) -> Instruction:
    bounded(word, 0xFFFFFF)
    op, a = word >> 20, word & 0xFFFFF
    if op >= len(NAMES) or a & ~MASKS[op] or (op == 8 and not a) or (op == 9 and not a >> 4):
        raise ValueError(f"illegal instruction 0x{word:06x}")
    args = ()
    if op in (1, 2):
        args = (a >> 8, a & 255)
    elif op == 3:
        args = (a >> 16, a & 65535)
    elif op in (4, 5):
        args = (a >> 16,)
    elif op == 6:
        args = (a & 7, a >> 3)
    elif op == 7:
        args = (a >> 16, a & 255)
    elif op == 8:
        args = (a,)
    elif op == 9:
        args = (a & 7, (a >> 3) & 1, a >> 4)
    elif op == 10:
        args = (a >> 16, a & 63)
    elif op == 11:
        args = (a,)
    return Instruction(NAMES[op], args)


def assemble(source: str) -> list[int]:
    labels, lines = {}, []
    for number, raw in enumerate(source.splitlines(), 1):
        line = re.split(r"[#;]", raw, maxsplit=1)[0].strip()
        if not line:
            continue
        if ":" in line:
            label, line = (part.strip() for part in line.split(":", 1))
            if not re.fullmatch(r"[A-Za-z_]\w*", label) or label in labels:
                raise ValueError(f"line {number}: invalid or duplicate label")
            labels[label] = len(lines)
        if line:
            lines.append((number, line))
    if not 1 <= len(lines) <= 64:
        raise ValueError("program must contain 1..64 instructions")
    words = []
    for number, line in lines:
        parts = line.split(None, 1)
        name = parts[0].upper()
        tokens = parts[1].split(",") if len(parts) > 1 else []
        try:
            args = []
            for index, token in enumerate(tokens):
                token = token.strip()
                is_target = name == "JMP" or name == "DJNZ" and index == 1
                args.append(labels[token] if is_target and token in labels else int(token, 0))
            words.append(encode(name, *args))
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
    return words


def disassemble(words: list[int]) -> str:
    return "\n".join(i.name + (" " + ", ".join(str(a) for a in i.operands) if i.operands else "")
                     for i in map(decode, words)) + "\n"


def image_bytes(words: list[int]) -> bytes:
    if not 1 <= len(words) <= 64:
        raise ValueError("program must contain 1..64 instructions")
    for word in words:
        decode(word)
    return b"".join(word.to_bytes(3, "big") for word in words)


def image_words(data: bytes) -> list[int]:
    if not data or len(data) % 3 or len(data) > 192:
        raise ValueError("image must contain 1..64 complete 24-bit words")
    words = [int.from_bytes(data[i:i+3], "big") for i in range(0, len(data), 3)]
    image_bytes(words)
    return words
