"""Host image loader for the V3 chip.

The loader drives the host protocol described in :mod:`protocol_emulator.v3.host`
through an :data:`Exchange`, one call per 48-clock SPI transfer. The chip
replies to each request on the *following* transfer, so every batch of requests
is pipelined: request N is sent while reply N-1 is collected, and the batch
ends with one ``NOP`` to collect the final reply. The reply returned by the
first transfer of a session is discarded.

A session is ``connect``, then ``load`` for each engine, then ``configure``
for each engine, then ``start``. ``load`` writes every one of the
:data:`~protocol_emulator.v3.PROGRAM_WORDS` words and reads them all back, so a
program is never started from memory that was not verified.
"""
from collections.abc import Callable, Iterable, Sequence

from . import PROGRAM_WORDS
from .host import COMMANDS, ENGINE_BIT, REJECT_BIT, VERSION_WORD, Frame
from .isa import Image

#: One SPI transfer: shifts a request out and returns the frame shifted back.
Exchange = Callable[[Frame], Frame]

_ENGINES = (0, 1)
_BYTE_MAX = 0xFF
_MAX_WATCHES = 2
_NAMES = {code: name for name, code in COMMANDS.items()}


class ProtocolError(RuntimeError):
    """The chip rejected a command, or its reply did not match the request."""


def _describe(request: Frame) -> str:
    code = request.cmd & ~ENGINE_BIT
    name = _NAMES.get(code, f"cmd 0x{request.cmd:02X}")
    engine = f" engine {1 if request.cmd & ENGINE_BIT else 0}" if code in _NAMES else ""
    return f"{name}{engine} addr {request.addr}"


def _check_engine(engine: int) -> None:
    if engine not in _ENGINES:
        raise ValueError(f"engine must be 0 or 1, got {engine}")


def _check_byte(name: str, value: int) -> None:
    if not 0 <= value <= _BYTE_MAX:
        raise ValueError(f"{name} must fit in 8 bits, got {value}")


def _command(name: str, engine: int) -> int:
    return COMMANDS[name] | (ENGINE_BIT if engine else 0)


def transact(exchange: Exchange, requests: Sequence[Frame]) -> list[Frame]:
    """Send ``requests`` and return their replies, aligned to the requests.

    Sends every request, then one trailing ``NOP`` to collect the last reply,
    for ``len(requests) + 1`` transfers in total. The reply to the first
    transfer belongs to an earlier session and is discarded.

    :param exchange: The SPI transfer function.
    :param requests: The frames to send, in order.
    :returns: One reply per request.
    :raises ProtocolError: If any reply is a rejection, or its command or
        address does not echo its request. The message names the request.
    """
    nop = Frame(COMMANDS["NOP"], 0, 0)
    received = [exchange(request) for request in (*requests, nop)]
    replies = received[1:]
    for request, reply in zip(requests, replies, strict=True):
        if reply.cmd & REJECT_BIT:
            raise ProtocolError(f"{_describe(request)} was rejected by the chip")
        if reply.cmd != request.cmd or reply.addr != request.addr:
            raise ProtocolError(
                f"{_describe(request)} got a mismatched reply "
                f"(cmd 0x{reply.cmd:02X}, addr {reply.addr})"
            )
    return replies


def connect(exchange: Exchange) -> None:
    """Read the version word and refuse a chip that is not V3.

    :param exchange: The SPI transfer function.
    :raises ProtocolError: If the version word differs from
        :data:`~protocol_emulator.v3.host.VERSION_WORD`; the message carries
        the received value in hex.
    """
    (reply,) = transact(exchange, [Frame(COMMANDS["VERSION"], 0, 0)])
    if reply.payload != VERSION_WORD:
        raise ProtocolError(
            f"unsupported chip version 0x{reply.payload:08X}, "
            f"expected 0x{VERSION_WORD:08X}"
        )


def load(exchange: Exchange, engine: int, image: Image) -> None:
    """Write all 256 words of ``image`` to an engine, then read them all back.

    :param exchange: The SPI transfer function.
    :param engine: The engine to load, 0 or 1.
    :param image: The assembled program; unused words are already ``HALT``.
    :raises ValueError: If ``engine`` is not 0 or 1.
    :raises ProtocolError: If a write is rejected, or a word read back differs
        from the word written; the message names the first mismatching
        address with the expected and read values.
    """
    _check_engine(engine)
    write = _command("WRITE", engine)
    read = _command("READ", engine)
    transact(
        exchange, [Frame(write, address, word) for address, word in enumerate(image.words)]
    )
    replies = transact(exchange, [Frame(read, address, 0) for address in range(PROGRAM_WORDS)])
    for address, (word, reply) in enumerate(zip(image.words, replies, strict=True)):
        if reply.payload != word:
            raise ProtocolError(
                f"readback mismatch at address {address}: "
                f"expected 0x{word:08X}, read 0x{reply.payload:08X}"
            )


def configure(
    exchange: Exchange,
    engine: int,
    owner: int,
    open_drain: int,
    watches: Sequence[tuple[int, int, int]] = (),
) -> None:
    """Set an engine's pin ownership, open-drain mask and edge watchers.

    Sends ``CONFIG`` with ``owner | open_drain << 8``, then one ``WATCH`` per
    entry of ``watches``, indexed by position. Call it while both engines are
    stopped.

    :param exchange: The SPI transfer function.
    :param engine: The engine to configure, 0 or 1.
    :param owner: Mask of the pins ``uio[7:0]`` the engine owns, 8 bits.
    :param open_drain: Mask of the engine's open-drain pins, 8 bits.
    :param watches: At most two ``(mask, value, edges)`` tuples, each field
        8 bits.
    :raises ValueError: If ``engine`` is not 0 or 1, a mask is wider than
        8 bits, or there are more than two watches. Nothing is sent.
    :raises ProtocolError: If the chip rejects a command.
    """
    _check_engine(engine)
    _check_byte("owner", owner)
    _check_byte("open_drain", open_drain)
    if len(watches) > _MAX_WATCHES:
        raise ValueError(f"at most {_MAX_WATCHES} watches are supported, got {len(watches)}")
    for index, (mask, value, edges) in enumerate(watches):
        _check_byte(f"watch {index} mask", mask)
        _check_byte(f"watch {index} value", value)
        _check_byte(f"watch {index} edges", edges)
    requests = [Frame(_command("CONFIG", engine), 0, owner | open_drain << 8)]
    requests.extend(
        Frame(_command("WATCH", engine), index, mask | value << 8 | edges << 16)
        for index, (mask, value, edges) in enumerate(watches)
    )
    transact(exchange, requests)


def start(exchange: Exchange, engines: Iterable[int]) -> None:
    """Start the given engines in the same cycle with one ``START`` frame.

    :param exchange: The SPI transfer function.
    :param engines: The engines to start, each 0 or 1; at least one.
    :raises ValueError: If the set is empty or holds an engine other than 0
        or 1. Nothing is sent.
    :raises ProtocolError: If the chip rejects the command, for example
        because a program is not fully written.
    """
    chosen = set(engines)
    if not chosen:
        raise ValueError("start needs at least one engine")
    mask = 0
    for engine in chosen:
        _check_engine(engine)
        mask |= 1 << engine
    transact(exchange, [Frame(COMMANDS["START"], 0, mask)])
