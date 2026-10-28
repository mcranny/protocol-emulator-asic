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
from typing import NamedTuple

from . import PROGRAM_WORDS
from .host import COMMANDS, ENGINE_BIT, REJECT_BIT, VERSION_WORD, WATCH_DISABLE, Frame
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
    """Invalidate an engine's image, write all 256 words, then read them back.

    The leading ``CLEAR`` drops the engine's validity bitmap so an interrupted
    reload leaves the image incomplete rather than mixing new words with stale
    valid ones, which would let a partial reload start.

    :param exchange: The SPI transfer function.
    :param engine: The engine to load, 0 or 1.
    :param image: The assembled program; unused words are already ``HALT``.
    :raises ValueError: If ``engine`` is not 0 or 1.
    :raises ProtocolError: If the clear or a write is rejected, or a word read
        back differs from the word written; the message names the first
        mismatching address with the expected and read values.
    """
    _check_engine(engine)
    clear = _command("CLEAR", engine)
    write = _command("WRITE", engine)
    read = _command("READ", engine)
    transact(
        exchange,
        [Frame(clear, 0, 0)]
        + [Frame(write, address, word) for address, word in enumerate(image.words)],
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
    watches: Sequence[tuple[int, ...]] = (),
) -> None:
    """Set an engine's pin ownership, open-drain mask and edge watchers.

    Sends ``CONFIG`` first. ``CONFIG`` clears both of that engine's watcher
    slots, so the ``WATCH`` frames that follow are the whole configuration.
    One ``WATCH`` is sent for each of the two slots: a supplied entry enables
    it, and a slot with no entry is disabled (payload bit 25). Call it while
    both engines are stopped.

    :param exchange: The SPI transfer function.
    :param engine: The engine to configure, 0 or 1.
    :param owner: Mask of the pins ``uio[7:0]`` the engine owns, 8 bits.
    :param open_drain: Mask of the engine's open-drain pins, 8 bits.
    :param watches: At most two watch tuples. Each is ``(mask, value, edges)``,
        each field 8 bits, with an optional fourth element ``abort`` (default
        ``False``). An abort watcher's event ends a WAITEDGE or WAITFOR
        interrupted; it is carried in payload bit 24.
    :raises ValueError: If ``engine`` is not 0 or 1, a mask is wider than
        8 bits, a watch tuple is not 3 or 4 elements, or there are more than two
        watches. Nothing is sent.
    :raises ProtocolError: If the chip rejects a command.
    """
    _check_engine(engine)
    _check_byte("owner", owner)
    _check_byte("open_drain", open_drain)
    if len(watches) > _MAX_WATCHES:
        raise ValueError(f"at most {_MAX_WATCHES} watches are supported, got {len(watches)}")
    for index, watch in enumerate(watches):
        if not 3 <= len(watch) <= 4:
            raise ValueError(
                f"watch {index} must be (mask, value, edges[, abort]), got {len(watch)} fields"
            )
        _check_byte(f"watch {index} mask", watch[0])
        _check_byte(f"watch {index} value", watch[1])
        _check_byte(f"watch {index} edges", watch[2])
    requests = [Frame(_command("CONFIG", engine), 0, owner | open_drain << 8)]
    for index in range(_MAX_WATCHES):
        if index < len(watches):
            watch = watches[index]
            payload = (
                watch[0]
                | watch[1] << 8
                | watch[2] << 16
                | (bool(watch[3]) if len(watch) == 4 else 0) << 24
            )
        else:
            payload = WATCH_DISABLE
        requests.append(Frame(_command("WATCH", engine), index, payload))
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


#: Most bytes one TXPUSH frame can carry.
_TX_FRAME_BYTES = 4


def push_tx(exchange: Exchange, engine: int, data: bytes) -> None:
    """Push ``data`` onto an engine's transmit queue, ready for ``PULL``.

    The queue takes at most four bytes per frame, so ``data`` is split into
    frames of up to four bytes, each carrying its byte count in ``addr`` and
    the bytes most significant first (the first byte at ``payload[31:24]``).
    One frame is 48 SPI clocks: at 1 MHz SPI a frame moves at most four bytes
    every 48 µs, which keeps up with 115200 baud but not sustained 1 Mbaud, so
    the caller must push ahead of the engine's consumption.

    :param exchange: The SPI transfer function.
    :param engine: The engine whose queue to fill, 0 or 1.
    :param data: The bytes to push; an empty value sends nothing.
    :raises ValueError: If ``engine`` is not 0 or 1.
    :raises ProtocolError: If the chip rejects a frame, for example because the
        queue did not have room for one of the groups.
    """
    _check_engine(engine)
    command = _command("TXPUSH", engine)
    requests = []
    for start in range(0, len(data), _TX_FRAME_BYTES):
        chunk = data[start : start + _TX_FRAME_BYTES]
        payload = 0
        for index, byte in enumerate(chunk):
            payload |= byte << (8 * (_TX_FRAME_BYTES - 1 - index))
        requests.append(Frame(command, len(chunk), payload))
    transact(exchange, requests)


def pop_rx(exchange: Exchange, engine: int) -> tuple[int, int] | None:
    """Pop the oldest ``(value, meta)`` record from an engine's receive queue.

    :param exchange: The SPI transfer function.
    :param engine: The engine whose queue to read, 0 or 1.
    :returns: The oldest ``(value, meta)`` record, or ``None`` when the queue
        is empty (the chip rejects the frame and nothing is popped).
    :raises ValueError: If ``engine`` is not 0 or 1.
    """
    _check_engine(engine)
    request = Frame(_command("RXPOP", engine), 0, 0)
    nop = Frame(COMMANDS["NOP"], 0, 0)
    # The reply to a frame rides out on the following transfer; the first
    # transfer returns an earlier, discarded reply.
    exchange(request)
    reply = exchange(nop)
    if reply.cmd & REJECT_BIT:
        return None
    return (reply.payload >> 8) & _BYTE_MAX, reply.payload & _BYTE_MAX


def set_filter(exchange: Exchange, mask: int) -> None:
    """Set the input-filter mask shared by all eight pins.

    A set bit passes that pin through the three-sample input filter; a clear
    bit feeds the raw pad to the pin cell. The reset mask is ``0xFF``. The new
    mask applies on the clock after the write.

    :param exchange: The SPI transfer function.
    :param mask: The 8-bit mask, bit ``n`` for ``uio[n]``.
    :raises ValueError: If ``mask`` is wider than 8 bits. Nothing is sent.
    :raises ProtocolError: If the chip rejects the write, which it does while
        either engine runs.
    """
    _check_byte("mask", mask)
    transact(exchange, [Frame(COMMANDS["FILTER"], 0, mask)])


def read_filter(exchange: Exchange) -> int:
    """Return the input-filter mask.

    :param exchange: The SPI transfer function.
    :returns: The 8-bit mask.
    """
    (reply,) = transact(exchange, [Frame(COMMANDS["FILTER"], 1, 0)])
    return reply.payload & _BYTE_MAX


#: Largest capture trigger mode: 0 immediate, 1 masked input change,
#: 2 masked engine-running flag, 3 aggregate error.
_TRIGGER_MODE_MAX = 3


def arm_capture(exchange: Exchange, mode: int = 0, mask: int = 0) -> None:
    """Arm capture, restarting it if it is already running.

    :param exchange: The SPI transfer function.
    :param mode: The trigger mode, 0 to 3.
    :param mask: The 8-bit trigger mask the mode applies.
    :raises ValueError: If ``mode`` is out of range or ``mask`` is wider than
        8 bits. Nothing is sent.
    """
    if not 0 <= mode <= _TRIGGER_MODE_MAX:
        raise ValueError(f"trigger mode must be 0 to {_TRIGGER_MODE_MAX}, got {mode}")
    _check_byte("mask", mask)
    transact(exchange, [Frame(COMMANDS["CAPARM"], 0, mode | mask << 8)])


def stop_capture(exchange: Exchange) -> None:
    """Stop capture, keeping the records taken so far.

    :param exchange: The SPI transfer function.
    """
    transact(exchange, [Frame(COMMANDS["CAPARM"], 1, 0)])


class EngineStatus(NamedTuple):
    """An engine's ``STATUS`` word, address 0."""

    running: bool
    halted: bool
    stalled: bool
    fault: int
    tx_level: int
    rx_level: int
    #: The eight pins' sticky missed-deadline flags, the same for either engine.
    missed: int


class CaptureStatus(NamedTuple):
    """Capture's ``STATUS`` words, addresses 1 and 2."""

    armed: bool
    triggered: bool
    truncated: bool
    reason: int
    record_count: int
    trigger_timestamp: int


def read_engine_status(exchange: Exchange, engine: int) -> EngineStatus:
    """Read and decode an engine's status word.

    :param exchange: The SPI transfer function.
    :param engine: The engine to read, 0 or 1.
    :returns: The decoded :class:`EngineStatus`.
    :raises ValueError: If ``engine`` is not 0 or 1. Nothing is sent.
    """
    _check_engine(engine)
    (reply,) = transact(exchange, [Frame(_command("STATUS", engine), 0, 0)])
    word = reply.payload
    return EngineStatus(
        running=bool(word & 1),
        halted=bool(word >> 1 & 1),
        stalled=bool(word >> 2 & 1),
        fault=word >> 3 & 0xFF,
        tx_level=word >> 11 & 0x1F,
        rx_level=word >> 16 & 0x1F,
        missed=word >> 21 & 0xFF,
    )


def read_capture_status(exchange: Exchange) -> CaptureStatus:
    """Read and decode capture's state and trigger timestamp.

    :param exchange: The SPI transfer function.
    :returns: The decoded :class:`CaptureStatus`.
    """
    state, stamp = transact(
        exchange, [Frame(COMMANDS["STATUS"], 1, 0), Frame(COMMANDS["STATUS"], 2, 0)]
    )
    word = state.payload
    return CaptureStatus(
        armed=bool(word & 1),
        triggered=bool(word >> 1 & 1),
        truncated=bool(word >> 2 & 1),
        reason=word >> 3 & 0x7,
        record_count=word >> 6 & 0x3F,
        trigger_timestamp=stamp.payload,
    )


#: Records the capture store holds.
_CAPTURE_DEPTH = 32


def read_capture(exchange: Exchange, count: int) -> list[tuple[int, int]]:
    """Read the first ``count`` capture records without disturbing capture.

    Each record is two ``CAPREAD`` frames: ``addr = index << 1`` returns the
    sample ``{pins_in, pins_out, pins_oe, flags}`` and ``addr = index << 1 | 1``
    returns the 32-bit timestamp. Take ``count`` from the ``record_count`` of
    :func:`read_capture_status`; a record at or past the chip's count reads
    as zero.

    :param exchange: The SPI transfer function.
    :param count: The number of records to read, 0 to 32.
    :returns: One ``(sample, timestamp)`` pair per record, oldest first.
    :raises ValueError: If ``count`` is out of range. Nothing is sent.
    """
    if not 0 <= count <= _CAPTURE_DEPTH:
        raise ValueError(f"count must be 0 to {_CAPTURE_DEPTH}, got {count}")
    read = COMMANDS["CAPREAD"]
    requests = [Frame(read, index << 1 | half, 0) for index in range(count) for half in (0, 1)]
    replies = transact(exchange, requests)
    return [(replies[i].payload, replies[i + 1].payload) for i in range(0, len(replies), 2)]
