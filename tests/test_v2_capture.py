import pytest

from protocol_emulator.v2.capture import Capture, Sample, TRIGGER


def test_initial_state_and_simultaneous_changes_share_one_timestamp():
    capture = Capture()
    capture.arm(Sample(255, 0, 0))
    capture.tick(Sample(255, 0, 0))
    capture.tick(Sample(254, 1, 1, 1))
    capture.tick(Sample(254, 1, 1))
    capture.stop()
    assert [record.cycle for record in capture.records] == [0, 1, 2, 3]
    assert capture.records[0].flags == TRIGGER
    assert capture.records[2].packed == 0x00000002FE010101
    assert capture.export()["complete"]
    assert "#80\nb11111110 !\nb00000001 \"\nb00000001 #\nb00000001 $" in capture.vcd()


@pytest.mark.parametrize("mode,mask,first,second", [
    ("pin", 2, Sample(1, 0, 0), Sample(3, 0, 0)),
    ("marker", 2, Sample(0, 0, 0, 1), Sample(0, 0, 0, 2)),
    ("error", 0, Sample(0, 0, 0, 1), Sample(0, 0, 0, 4)),
])
def test_post_trigger_only(mode, mask, first, second):
    capture = Capture()
    capture.arm(Sample(0, 0, 0), mode, mask)
    capture.tick(first)
    assert capture.records == []
    capture.tick(second)
    assert capture.trigger_cycle == 2
    assert len(capture.records) == 1
    assert capture.records[0].flags & TRIGGER
    capture.stop()
    assert capture.complete


def test_capacity_freezes_and_cannot_be_misreported_complete():
    capture = Capture(depth=2)
    capture.arm(Sample(0, 0, 0))
    capture.tick(Sample(1, 0, 0))
    before = capture.export(allow_incomplete=True)
    capture.tick(Sample(2, 0, 0))
    capture.stop()
    assert capture.export(allow_incomplete=True) == before
    assert before["truncated"] and before["reason"] == "capacity"
    with pytest.raises(ValueError, match="incomplete"):
        capture.export()


def test_timestamp_exhaustion_never_wraps_even_while_waiting_for_trigger():
    capture = Capture(timestamp_bits=3)
    capture.arm(Sample(0, 0, 0), "pin", 1)
    for _ in range(10):
        capture.tick(Sample(0, 0, 0))
    assert capture.cycle == 7 and capture.reason == "timestamp"
    assert capture.truncated and not capture.triggered
    capture.reset()
    assert capture.cycle == 0 and capture.records == []
    assert not capture.armed and not capture.truncated


def test_arm_validation_does_not_destroy_existing_capture():
    capture = Capture()
    capture.arm(Sample(1, 2, 3))
    before = capture.export(allow_incomplete=True)
    with pytest.raises(ValueError):
        capture.arm(Sample(1, 2, 3), "unknown")
    assert capture.export(allow_incomplete=True) == before


def test_consecutive_identical_markers_are_distinct_events():
    capture = Capture()
    marker = Sample(255, 0, 0, 1)
    capture.arm(marker, "marker", 1)
    for _ in range(3):
        capture.tick(marker)
    assert [record.cycle for record in capture.records] == [0, 1, 2, 3]
    assert [record.flags for record in capture.records] == [9, 1, 1, 1]


def test_disable_preserves_diagnostic_records_without_claiming_completion():
    capture = Capture()
    capture.arm(Sample(255, 0, 0))
    capture.disable()
    capture.stop()
    assert capture.reason == "disabled" and len(capture.records) == 1
    with pytest.raises(ValueError, match="incomplete"):
        capture.export()


def test_observation_does_not_change_engine_waveform():
    from protocol_emulator.v2.model import Device
    from protocol_emulator.v2.isa import assemble
    devices = [Device(64), Device(64)]
    for device in devices:
        device.configure([1, 2])
        for index, engine in enumerate(device.engines):
            engine.load(assemble(f"DRIVE {1 << index}, {1 << index}\nMARK 1\nDELAY 2\nHALT"))
        device.start(3)
    capture = Capture()
    capture.arm(Sample(255, *devices[0].pins))
    for _ in range(10):
        sample = Sample(255, *devices[0].pins,
                        sum(bool(e.marker) << i for i, e in enumerate(devices[0].engines)))
        capture.tick(sample)
        assert devices[0].tick(255) == devices[1].tick(255)
    capture.stop()
    assert capture.complete
    assert [(r.cycle, r.outputs, r.enables) for r in capture.records if r.outputs] == [
        (2, 3, 3), (3, 3, 3), (4, 3, 3)]
    assert [r.cycle for r in capture.records if r.flags & 3] == [3]


def test_device_capture_sees_shared_error_after_execution_edge():
    from protocol_emulator.v2.model import Device
    from protocol_emulator.v2.isa import assemble
    device = Device(64)
    device.configure([1, 0])
    device.engines[0].load(assemble("DRIVE 1, 1\nJMP 1"))
    device.start(1)
    device.tick(255, capture_arm=("error", 255))
    device.tick(255, frame_error=True)
    assert not device.capture.triggered
    device.tick(255)
    assert device.capture.trigger_cycle == 2
    first = device.capture.records[0]
    assert first.outputs == first.enables == 0
    assert first.flags == 12
    device.tick(255, capture_stop=True)
    assert device.capture.complete
