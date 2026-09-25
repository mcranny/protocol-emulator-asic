import pytest

from protocol_emulator.host import SimulationTransport, Device as V1Device, DeviceError
from protocol_emulator.v2.host import BatchError, Device, VERSION, command_frame, connect, tx_commands


class Wire:
    """Command-level fake with a real one-response pipeline, no engine timing."""
    def __init__(self, version=VERSION):
        self.version = version
        self.pending = bytes(5)
        self.commands = []

    def exchange(self, frame):
        self.commands.append(frame)
        previous = self.pending
        command, address = frame[:2]
        payload = int.from_bytes(frame[2:], "big")
        result = self.version if command == 9 else payload
        self.pending = bytes((command, address)) + result.to_bytes(3, "big")
        return previous


def test_probe_dispatch_and_unknown_version():
    assert isinstance(connect(SimulationTransport()), V1Device)
    assert isinstance(connect(Wire()), Device)
    with pytest.raises(DeviceError, match="unsupported"):
        connect(Wire(0x030340))


def test_packed_order_and_pipeline_frame_budget():
    wire = Wire()
    device = Device(wire)
    outgoing = bytes(range(8))
    frames = tx_commands(1, outgoing)
    assert frames == [bytes.fromhex("1280020100"), bytes.fromhex("1280050403"), bytes.fromhex("1180000706")]
    commands = frames + [command_frame(0x13, 1)] * 8
    assert len(device.batch(commands)) == 11
    assert len(wire.commands) == 12
    assert len(wire.commands) * (40 + 1) == 492  # us at 1 MHz, 1 us gaps/frame


def test_invalid_image_cannot_partially_program():
    wire = Wire()
    device = Device(wire)
    with pytest.raises(ValueError):
        device.load(0, [0, 0xF00000])
    assert not wire.commands


def test_batch_does_not_retry_rejected_transfer():
    wire = Wire()
    original = wire.exchange
    def reject(frame):
        result = original(frame)
        if frame[0] == 0x12:
            wire.pending = bytes((0x92, frame[1], 0, 0, 0))
        return result
    wire.exchange = reject
    with pytest.raises(DeviceError):
        Device(wire).fifo_write(0, b"abc")
    assert len(wire.commands) == 2


def test_invalid_batch_is_rejected_before_any_write():
    wire = Wire()
    device = Device(wire)
    for frames in ([command_frame(1), b"short!"], [command_frame(1)] * 65):
        with pytest.raises(ValueError):
            device.batch(frames)
    with pytest.raises(ValueError):
        device.fifo_write(2, b"")
    assert wire.commands == []


def test_transport_failure_preserves_completed_responses_without_retry():
    wire = Wire()
    original = wire.exchange
    def fail(frame):
        if len(wire.commands) == 2:
            raise OSError("link lost")
        return original(frame)
    wire.exchange = fail
    with pytest.raises(BatchError) as caught:
        Device(wire).batch([command_frame(1, value=i) for i in (7, 8, 9)])
    assert caught.value.completed == (7,)
    assert caught.value.submitted == 3
    assert isinstance(caught.value.__cause__, OSError)
    assert len(wire.commands) == 2


def test_start_rejects_wrong_version_before_start_command():
    wire = Wire(0x030340)
    with pytest.raises(DeviceError, match="unsupported"):
        Device(wire).start()
    assert all(frame[0] != 0x20 for frame in wire.commands)


class CaptureWire(Wire):
    def __init__(self, records, end, reason=3, truncated=False):
        super().__init__()
        self.records, self.end = records, end
        self.status = len(records) << 6 | reason << 3 | int(truncated) << 2 | 2

    def exchange(self, frame):
        previous = super().exchange(frame)
        command, address = frame[:2]
        if command == 0x32:
            value = self.status
        elif command == 0x33:
            full = self.end if address < 2 else self.records[0].cycle
            value = full >> 24 if address & 1 else full & 0xFFFFFF
        elif command in (0x35, 0x36, 0x37):
            value = self.records[address].packed >> ((command - 0x35) * 24) & 0xFFFFFF
        else:
            return previous
        self.pending = bytes((command, address)) + value.to_bytes(3, "big")
        return previous


def test_capture_read_preserves_full_timestamp_and_fields():
    from protocol_emulator.v2.capture import Record
    records = [Record(0xFE001234, 0xAA, 0x55, 0x81, 8),
               Record(0xFE001239, 0xA5, 0x33, 0x01, 0)]
    wire = CaptureWire(records, 0xFE00123F)
    result = Device(wire).capture_read()
    assert result["end_cycle"] == 0xFE00123F
    assert result["trigger_cycle"] == 0xFE001234
    assert result["records"] == [record.__dict__ for record in records]
    assert result["complete"]


def test_full_capture_read_splits_batches_and_requires_incomplete_opt_in():
    from protocol_emulator.v2.capture import Record
    records = [Record(i, i, i, i, 8 if i == 0 else 0) for i in range(32)]
    wire = CaptureWire(records, 31, reason=5, truncated=True)
    with pytest.raises(DeviceError, match="incomplete"):
        Device(wire).capture_read()
    wire.commands.clear()
    result = Device(wire).capture_read(allow_incomplete=True)
    assert not result["complete"] and result["truncated"]
    assert len(result["records"]) == 32
    assert sum(frame[0] in (0x35, 0x36, 0x37) for frame in wire.commands) == 96


def test_capture_rejects_active_and_inconsistent_readout():
    from protocol_emulator.v2.capture import Record
    wire = CaptureWire([Record(5, 0, 0, 0, 8)], 4)
    wire.status |= 1
    with pytest.raises(DeviceError, match="stop"):
        Device(wire).capture_read()
    wire.status &= ~1
    with pytest.raises(DeviceError, match="inconsistent"):
        Device(wire).capture_read()
