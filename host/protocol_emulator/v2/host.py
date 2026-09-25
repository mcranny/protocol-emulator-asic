"""V2 host commands over the preserved 40-clock physical SPI framing.

Packed TX and pipelined RX remove per-byte request/NOP round trips. RX data
are committed only when the following complete frame has delivered the reply.
"""
from ..host import DeviceError
from .isa import decode, encode

VERSION = 0x020240


class BatchError(DeviceError):
    """A pipelined batch failed; submitted requests must not be blindly retried."""
    def __init__(self, message, completed, submitted):
        super().__init__(message)
        self.completed = tuple(completed)
        self.submitted = submitted


def command_frame(command, engine=0, address=0, value=0):
    if engine not in (0, 1) or not 0 <= address < 64 or not 0 <= value <= 0xFFFFFF:
        raise ValueError("invalid V2 request")
    if not 0 <= command < 128:
        raise ValueError("invalid V2 command")
    return bytes((command, engine << 7 | address)) + value.to_bytes(3, "big")


def tx_commands(engine, data):
    command_frame(0x10, engine)  # Validate the engine even for an empty batch.
    data = bytes(data)
    if len(data) > 16:
        raise ValueError("a batch may contain at most 16 TX bytes")
    return [command_frame(0x0F + len(part), engine, value=int.from_bytes(part, "little"))
            for i in range(0, len(data), 3) if (part := data[i:i + 3])]


def parse_response(request, response, *, allow_empty=False):
    if len(response) != 5 or response[1] != request[1]:
        raise DeviceError("short/mismatched V2 response")
    if response[0] != request[0]:
        if allow_empty and request[0] == 0x13 and response[0] == 0x93:
            return None
        raise DeviceError(f"rejected V2 command: {response.hex()}")
    return int.from_bytes(response[2:], "big")


class Device:
    def __init__(self, transport):
        self.transport = transport

    def batch(self, requests, allow_empty=False):
        requests = list(requests)
        if len(requests) > 64:
            raise ValueError("a batch may contain at most 64 requests")
        if any(not isinstance(frame, bytes) or len(frame) != 5 or frame[0] >= 128
               for frame in requests):
            raise ValueError("invalid V2 batch frame")
        if not requests:
            return []
        results = []
        previous = None
        for submitted, frame in enumerate(requests + [bytes(5)], 1):
            try:
                reply = self.transport.exchange(frame)
            except Exception as error:
                raise BatchError("V2 transport failed; last request delivery is unknown",
                                 results, min(submitted, len(requests))) from error
            if previous is not None:
                try:
                    results.append(parse_response(previous, reply, allow_empty=allow_empty))
                except DeviceError as error:
                    raise BatchError(str(error), results, min(submitted, len(requests))) from error
            previous = frame
        return results

    def request(self, command, engine=0, address=0, value=0):
        return self.batch([command_frame(command, engine, address, value)])[0]

    def check_version(self):
        if self.request(9) != VERSION:
            raise DeviceError("unsupported V2 host/ISA version")

    def load(self, engine, words):
        words = list(words)
        if not 1 <= len(words) <= 64:
            raise ValueError("V2 candidate has 64 program words")
        for word in words:
            insn = decode(word)
            if insn.name in ("JMP", "BRPIN", "BREQ", "BRC", "BRDIFF", "CALL", "WAITBR"):
                target = insn.operands[0]
            elif insn.name == "DJNZ":
                target = insn.operands[1]
            else:
                continue
            if target >= 64:
                raise ValueError("branch outside V2 candidate program memory")
        self.check_version()
        if self.request(6, engine) & 1:
            raise DeviceError("stop before loading")
        padded = words + [encode("HALT")] * (64 - len(words))
        self.batch([command_frame(1, engine, address, word) for address, word in enumerate(padded)])
        if self.batch([command_frame(2, engine, address) for address in range(64)]) != padded:
            raise DeviceError("V2 program verification failed")

    def configure(self, engine, owner, open_drain=0):
        if not 0 <= owner <= 255 or not 0 <= open_drain <= 255 or open_drain & ~owner:
            raise ValueError("invalid ownership/open-drain masks")
        return self.request(0x21, engine, value=owner | open_drain << 8)

    def start(self, mask=3):
        if mask not in (1, 2, 3):
            raise ValueError("invalid start mask")
        self.check_version()
        return self.request(0x20, value=mask)

    def stop(self, engine):
        return self.request(4, engine)

    def reset(self, engine):
        return self.request(5, engine)

    def status(self, engine):
        return self.request(6, engine)

    def fifo_write(self, engine, data):
        results = self.batch(tx_commands(engine, data))
        return sum((result >> 5) & 3 for result in results)

    def fifo_read(self, engine, count=16):
        if not isinstance(count, int) or not 0 <= count <= 16:
            raise ValueError("invalid RX batch length")
        results = self.batch([command_frame(0x13, engine)] * count, allow_empty=True)
        return [(result & 255, result >> 8 & 255) for result in results if result is not None]


def connect(transport):
    """Probe before selecting a V1 or V2 backend; never silently reinterpret."""
    from ..host import Device as V1Device, VERSION as V1_VERSION
    transport.exchange(command_frame(9))
    version = parse_response(command_frame(9), transport.exchange(bytes(5)))
    if version == VERSION:
        return Device(transport)
    if version == V1_VERSION:
        return V1Device(transport)
    raise DeviceError(f"unsupported device version: {version:#08x}")
