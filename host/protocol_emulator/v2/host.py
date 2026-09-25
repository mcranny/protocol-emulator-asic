"""V2 host commands over the preserved 40-clock physical SPI framing.

Packed TX and pipelined RX remove per-byte request/NOP round trips. RX data
are committed only when the following complete frame has delivered the reply.
"""
from dataclasses import asdict

from ..host import DeviceError
from .capture import Record
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

    def capabilities(self):
        self.check_version()
        queues, features = self.batch([command_frame(10), command_frame(11)])
        return {"host_version": 2, "isa_version": 2, "program_words": 64,
                "engines": queues >> 16, "tx_depth": queues >> 8 & 255,
                "rx_depth": queues & 255, "capture_depth": features >> 16,
                "features": features & 65535}

    def capture_arm(self, mode="immediate", mask=255):
        modes = ("immediate", "pin", "marker", "error")
        if mode not in modes or type(mask) is not int or not 0 <= mask <= 255:
            raise ValueError("invalid capture trigger")
        if not self.capabilities()["features"] & 1:
            raise DeviceError("capture is not supported")
        return self.request(0x30, value=mask << 8 | modes.index(mode))

    def capture_stop(self):
        return self.request(0x31)

    def capture_read(self, *, allow_incomplete=False):
        status = self.request(0x32)
        armed, triggered, truncated = (bool(status & (1 << bit)) for bit in range(3))
        reason_index, count = status >> 3 & 7, status >> 6 & 63
        reasons = ("reset", "armed", "capturing", "stopped", "untriggered", "capacity", "timestamp", "disabled")
        if armed:
            raise DeviceError("stop capture before readout")
        if reason_index >= len(reasons) or count > 32 or status >> 12:
            raise DeviceError("invalid capture status")
        complete = triggered and not truncated and reasons[reason_index] == "stopped"
        if not complete and not allow_incomplete:
            raise DeviceError("capture is incomplete")
        times = self.batch([command_frame(0x33, address=i) for i in range(4)])
        end = times[0] | times[1] << 24
        trigger = times[2] | times[3] << 24
        requests = [command_frame(command, address=i) for i in range(count)
                    for command in (0x35, 0x36, 0x37)]
        chunks = []
        for offset in range(0, len(requests), 64):
            chunks.extend(self.batch(requests[offset:offset + 64]))
        records = []
        for offset in range(0, len(chunks), 3):
            word = chunks[offset] | chunks[offset + 1] << 24 | chunks[offset + 2] << 48
            records.append(Record(word >> 32, word >> 24 & 255, word >> 16 & 255,
                                  word >> 8 & 255, word & 255))
        if (end > 0xFFFFFFFF or trigger > end or bool(records) != triggered
                or (records and (records[0].cycle != trigger or not records[0].flags & 8))
                or any(r.flags & 8 for r in records[1:])
                or any(r.cycle > end for r in records)
                or any(a.cycle >= b.cycle for a, b in zip(records, records[1:]))):
            raise DeviceError("inconsistent capture readout")
        return {"schema": 1, "clock_hz": 25_000_000, "sampling": "before_execution",
                "timestamp_origin": "arm", "maximum_cycle": 0xFFFFFFFF,
                "end_cycle": end, "trigger_cycle": trigger if triggered else None,
                "complete": complete, "truncated": truncated, "reason": reasons[reason_index],
                "records": [asdict(record) for record in records]}


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
