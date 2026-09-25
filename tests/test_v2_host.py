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
