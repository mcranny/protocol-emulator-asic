"""Host frame codec for the V3 SPI transport.

The host talks to the chip over SPI mode 0, MSB first, in frames of exactly
48 clocks. A frame carries three fields, most significant first:

====== =========== =====================================
Bits   Field       Meaning
====== =========== =====================================
47..40 ``cmd``     Command byte (see :data:`COMMANDS`)
39..32 ``addr``    Register or memory address
31..0  ``payload`` 32-bit data word
====== =========== =====================================

Bit 7 of the command byte in a reply flags rejection
(:data:`REJECT_BIT`), so engine selection uses bit 6 (:data:`ENGINE_BIT`).
No command code may set either bit.

This module is a pure codec. It performs no I/O.
"""

from typing import NamedTuple

#: Number of SPI clocks, and therefore bits, in one frame.
FRAME_BITS: int = 48

#: Bit 7 of the reply command byte: set when the chip rejects the command.
REJECT_BIT: int = 0x80

#: Bit 6 of the command byte: selects the engine a command addresses.
ENGINE_BIT: int = 0x40

#: Command names mapped to their command-byte codes.
COMMANDS: dict[str, int] = {
    "NOP": 0x00,
    "WRITE": 0x01,
    "READ": 0x02,
    "CONFIG": 0x03,
    "WATCH": 0x04,
    "START": 0x05,
    "STOP": 0x06,
    "VERSION": 0x07,
    "TXPUSH": 0x08,
    "RXPOP": 0x09,
    "CLEAR": 0x0A,
}

#: Payload carried by the reply to the ``VERSION`` command (major version 3).
VERSION_WORD: int = 0x03000000

_CMD_BITS = 8
_ADDR_BITS = 8
_PAYLOAD_BITS = 32


class Frame(NamedTuple):
    """One decoded host frame."""

    cmd: int
    addr: int
    payload: int


def _check(name: str, value: int, width: int) -> None:
    if not 0 <= value < (1 << width):
        raise ValueError(f"{name} must fit in {width} bits unsigned, got {value}")


def pack(cmd: int, addr: int, payload: int) -> int:
    """Pack a frame into a 48-bit integer, first-transmitted bit at bit 47.

    :param cmd: Command byte, 0 to 0xFF.
    :param addr: Address byte, 0 to 0xFF.
    :param payload: Data word, 0 to 0xFFFFFFFF.
    :returns: The frame as an integer in ``[0, 2**48)``.
    :raises ValueError: If a field is negative or too wide; the message names
        the field (``cmd``, ``addr`` or ``payload``).
    """
    _check("cmd", cmd, _CMD_BITS)
    _check("addr", addr, _ADDR_BITS)
    _check("payload", payload, _PAYLOAD_BITS)
    return (cmd << (_ADDR_BITS + _PAYLOAD_BITS)) | (addr << _PAYLOAD_BITS) | payload


def unpack(bits: int) -> Frame:
    """Split a 48-bit integer into its frame fields.

    :param bits: The frame as an integer in ``[0, 2**48)``.
    :returns: The decoded :class:`Frame`.
    :raises ValueError: If ``bits`` is negative or does not fit in 48 bits.
    """
    if not 0 <= bits < (1 << FRAME_BITS):
        raise ValueError(f"frame must fit in {FRAME_BITS} bits unsigned, got {bits}")
    return Frame(
        cmd=bits >> (_ADDR_BITS + _PAYLOAD_BITS),
        addr=(bits >> _PAYLOAD_BITS) & 0xFF,
        payload=bits & 0xFFFFFFFF,
    )
