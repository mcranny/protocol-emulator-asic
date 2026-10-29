"""Capture keeps the V2 record and trigger semantics on the shared timestamp.

The reference is ``src/v2_capture.v`` at the ``v2`` tag. V3 differs in two
ways that these tests pin down: timestamps are the caller's shared 32-bit
value rather than a private counter restarted on arm, and there is no
stop-on-timestamp-saturation. There is no ring wraparound in either design:
a full capture stops and keeps its first 32 records.
"""
from protocol_emulator.v3 import model

Reason = model.CaptureReason


def pack(timestamp: int, pins_in: int, pins_out: int, pins_oe: int, flags: int) -> int:
    return (timestamp << 32) | (pins_in << 24) | (pins_out << 16) | (pins_oe << 8) | flags


def state(capture: model.Capture) -> tuple[bool, bool, bool]:
    """Return ``(armed, triggered, truncated)``.

    Reading through a call stops mypy carrying the narrowing of an earlier
    ``assert capture.triggered`` across later method calls that change it.
    """
    return capture.armed, capture.triggered, capture.truncated


def arm(
    capture: model.Capture,
    mode: int = 0,
    mask: int = 0xFF,
    timestamp: int = 100,
    pins_in: int = 0,
    pins_out: int = 0,
    pins_oe: int = 0,
    flags: int = 0,
) -> None:
    capture.arm(timestamp, mode, mask, pins_in, pins_out, pins_oe, flags)


def test_a_fresh_capture_is_idle_and_empty() -> None:
    capture = model.Capture()
    assert not capture.armed
    assert capture.count == 0
    assert capture.reason == Reason.RESET
    assert capture.read(0) == 0
    assert capture.record(5, 1, 1, 1, 1) is False
    assert capture.count == 0


def test_an_immediate_trigger_records_the_arming_sample() -> None:
    capture = model.Capture()
    arm(capture, mode=0, timestamp=1000, pins_in=0x12, pins_out=0x34, pins_oe=0x56, flags=0x01)
    assert capture.armed and capture.triggered
    assert capture.count == 1
    assert capture.trigger_timestamp == 1000
    assert capture.reason == Reason.CAPTURING
    # Flag bit 3 carries the trigger annotation.
    assert capture.read(0) == pack(1000, 0x12, 0x34, 0x56, 0x01 | 0x08)


def test_arming_without_a_trigger_records_nothing() -> None:
    capture = model.Capture()
    arm(capture, mode=2, mask=0x03)
    assert capture.armed and not capture.triggered
    assert capture.count == 0
    assert capture.trigger_timestamp == 0
    assert capture.reason == Reason.ARMED
    assert capture.read(0) == 0


def test_samples_before_the_trigger_are_not_stored() -> None:
    capture = model.Capture()
    arm(capture, mode=2, mask=0x03)
    for step in range(1, 6):
        assert capture.record(100 + step, step, 0, 0, 0x00) is False
    assert capture.count == 0
    assert not capture.triggered


def test_the_trigger_annotation_is_only_on_the_trigger_record() -> None:
    capture = model.Capture()
    arm(capture, mode=2, mask=0x03)
    assert capture.record(101, 0, 0, 0, 0x01) is True
    assert capture.record(102, 1, 0, 0, 0x00) is True
    assert capture.read(0) & 0x08
    assert not capture.read(1) & 0x08


def test_flag_bit_three_is_never_sampled() -> None:
    capture = model.Capture()
    arm(capture, mode=3)
    capture.record(101, 0, 0, 0, 0x0C)  # trigger on flag 2; bit 3 is stripped
    assert capture.read(0) & 0xFF == 0x04 | 0x08  # sampled 0x04, annotation 0x08
    assert capture.record(102, 0, 0, 0, 0x04) is True  # the sample after the trigger
    assert capture.count == 2
    # Bit 3 alone differs from the previous sample, which must not count.
    assert capture.record(103, 0, 0, 0, 0x0C) is False
    assert capture.count == 2

def test_pin_change_trigger_respects_the_mask() -> None:
    capture = model.Capture()
    arm(capture, mode=1, mask=0x04, pins_in=0x00)
    assert capture.record(101, 0x03, 0, 0, 0) is False  # bits 0,1 masked out
    assert not capture.triggered
    assert capture.record(102, 0x07, 0, 0, 0) is True  # bit 2 moved
    _, triggered, _ = state(capture)
    assert triggered
    assert capture.trigger_timestamp == 102
    assert capture.reason == Reason.CAPTURING


def test_pin_change_trigger_is_not_evaluated_on_the_arming_sample() -> None:
    capture = model.Capture()
    arm(capture, mode=1, mask=0xFF, pins_in=0xFF)
    assert not capture.triggered
    assert capture.count == 0


def test_pin_change_compares_with_the_previous_sample_not_the_arming_one() -> None:
    capture = model.Capture()
    arm(capture, mode=1, mask=0x01, pins_in=0x00)
    capture.record(101, 0x02, 0, 0, 0)  # unmasked change
    assert not capture.triggered
    capture.record(102, 0x02, 0, 0, 0)  # level held: no edge
    assert not capture.triggered
    capture.record(103, 0x03, 0, 0, 0)
    assert capture.triggered


def test_flag_trigger_uses_flags_one_and_zero_under_the_mask() -> None:
    capture = model.Capture()
    arm(capture, mode=2, mask=0x02)
    capture.record(101, 0, 0, 0, 0x01)  # bit 0 is masked out
    assert not capture.triggered
    capture.record(102, 0, 0, 0, 0x04)  # bit 2 is outside the 0x03 window
    assert not capture.triggered
    capture.record(103, 0, 0, 0, 0x02)
    assert capture.triggered


def test_engine_flag_two_triggers_in_mode_three_regardless_of_mask() -> None:
    capture = model.Capture()
    arm(capture, mode=3, mask=0x00)
    capture.record(101, 0, 0, 0, 0x03)
    assert not capture.triggered
    capture.record(102, 0, 0, 0, 0x04)
    _, triggered, _ = state(capture)
    assert triggered
    assert capture.trigger_timestamp == 102


def test_the_trigger_fires_once_and_keeps_its_first_timestamp() -> None:
    capture = model.Capture()
    arm(capture, mode=0, timestamp=10)
    capture.record(11, 0, 0, 0, 0)
    capture.record(12, 0, 0, 0, 0)
    assert capture.trigger_timestamp == 10


def test_after_the_trigger_only_changes_are_stored() -> None:
    capture = model.Capture()
    arm(capture, mode=0, timestamp=10, pins_in=0x00)
    assert capture.count == 1
    # The sample after the trigger is always taken (V2 clear_trigger term).
    assert capture.record(11, 0x00, 0, 0, 0) is True
    # Now steady: nothing is stored while the sample is unchanged.
    assert capture.record(12, 0x00, 0, 0, 0) is False
    assert capture.record(13, 0x00, 0, 0, 0) is False
    assert capture.count == 2
    assert capture.record(14, 0x01, 0, 0, 0) is True
    assert capture.record(15, 0x01, 0, 0, 0) is False
    assert capture.count == 3
    assert capture.read(2) == pack(14, 0x01, 0, 0, 0)


def test_output_and_enable_changes_are_stored_too() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 0, 0, 0, 0)
    assert capture.record(12, 0, 0x80, 0, 0) is True
    assert capture.record(13, 0, 0x80, 0x80, 0) is True
    assert capture.record(14, 0, 0x80, 0x80, 0x10) is True
    assert capture.read(4) == pack(14, 0, 0x80, 0x80, 0x10)


def test_samples_are_stored_every_cycle_while_flags_one_or_zero_are_set() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 0, 0, 0, 0)  # the sample after the trigger
    count = capture.count
    for step in range(12, 16):
        # The first changes the sample; the rest repeat it, and flag bit 0
        # forces a store each cycle.
        assert capture.record(step, 0, 0, 0, 0x01) is True
    assert capture.count == count + 4


def test_capacity_is_thirty_two_and_a_full_capture_stops_and_is_truncated() -> None:
    capture = model.Capture()
    arm(capture, timestamp=0, pins_in=0)
    for index in range(1, 31):
        assert capture.record(index, index & 0xFF, 0, 0, 0) is True
    assert capture.count == 31
    assert capture.armed and not capture.truncated
    assert capture.record(31, 0xAA, 0, 0, 0) is True
    assert capture.count == model.CAPTURE_DEPTH == 32
    armed, _, truncated = state(capture)
    assert not armed
    assert truncated
    assert capture.reason == Reason.CAPACITY


def test_there_is_no_wraparound_the_first_records_are_kept() -> None:
    capture = model.Capture()
    arm(capture, timestamp=0)
    first = capture.read(0)
    for index in range(1, 40):
        capture.record(index, index & 0xFF, 0, 0, 0)
    assert capture.count == 32
    assert capture.read(0) == first
    assert capture.read(31) == pack(31, 31, 0, 0, 0)
    assert capture.read(32) == 0
    # A full capture is no longer armed, so further samples are ignored.
    assert capture.record(99, 0x55, 0, 0, 0) is False
    assert capture.count == 32


def test_stopping_after_the_trigger_reports_stopped() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 1, 0, 0, 0, stop=True)
    assert not capture.armed
    assert capture.reason == Reason.STOPPED
    assert not capture.truncated
    assert capture.count == 1


def test_stopping_before_the_trigger_reports_untriggered() -> None:
    capture = model.Capture()
    arm(capture, mode=2, mask=0x03)
    capture.record(11, 0, 0, 0, 0, stop=True)
    assert not capture.armed
    assert capture.reason == Reason.UNTRIGGERED
    assert capture.count == 0


def test_disable_ends_the_capture_and_outranks_stop() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 1, 0, 0, 0, stop=True, disable=True)
    assert not capture.armed
    assert capture.reason == Reason.DISABLED


def test_the_stopping_sample_is_not_stored() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 0, 0, 0, 0)
    capture.record(12, 0xFF, 0xFF, 0xFF, 0xFF, stop=True)
    assert capture.count == 2


def test_records_survive_a_stop_and_samples_after_it_are_ignored() -> None:
    capture = model.Capture()
    arm(capture, timestamp=5, pins_in=1)
    capture.record(6, 2, 0, 0, 0, stop=True)
    assert capture.record(7, 3, 0, 0, 0) is False
    assert capture.count == 1
    assert capture.read(0) == pack(5, 1, 0, 0, 0x08)
    assert capture.reason == Reason.STOPPED


def test_rearming_discards_the_previous_capture() -> None:
    capture = model.Capture()
    arm(capture, timestamp=1)
    for index in range(2, 10):
        capture.record(index, index, 0, 0, 0)
    assert capture.count > 1
    arm(capture, mode=2, mask=0x03, timestamp=50)
    assert capture.count == 0
    assert capture.read(0) == 0
    assert capture.read(5) == 0
    assert not capture.triggered and not capture.truncated
    assert capture.armed
    assert capture.reason == Reason.ARMED


def test_rearming_clears_a_truncated_capture() -> None:
    capture = model.Capture()
    arm(capture)
    for index in range(1, 40):
        capture.record(index, index & 0xFF, 0, 0, 0)
    assert capture.truncated
    arm(capture, mode=2, mask=0x03)
    _, _, truncated = state(capture)
    assert not truncated
    assert capture.armed


def test_records_carry_the_callers_shared_timestamp_unchanged() -> None:
    capture = model.Capture()
    # V2 stamped arm as 0 and counted cycles since arm; V3 stamps the shared
    # absolute value, so a late arm shows its real time.
    arm(capture, timestamp=0x01234567)
    capture.record(0x01234568, 1, 0, 0, 0)
    capture.record(0x01234570, 2, 0, 0, 0)
    assert [capture.read(index) >> 32 for index in range(3)] == [
        0x01234567,
        0x01234568,
        0x01234570,
    ]
    assert capture.trigger_timestamp == 0x01234567


def test_the_timestamp_wraps_at_thirty_two_bits_without_ending_the_capture() -> None:
    capture = model.Capture()
    arm(capture, timestamp=0xFFFFFFFE)
    capture.record(0xFFFFFFFF, 1, 0, 0, 0)
    assert capture.record(0x1_0000_0000, 2, 0, 0, 0) is True  # wrapped to 0
    assert capture.armed and not capture.truncated
    assert [capture.read(index) >> 32 for index in range(3)] == [
        0xFFFFFFFE,
        0xFFFFFFFF,
        0x00000000,
    ]


def _capture_across_timebase(start_epoch: int, ticks: int) -> tuple[list[int], list[int]]:
    """Capture one sample per tick of a real timebase starting just before a wrap.

    Returns the timebase's ``wide`` value at each sample and the timestamps
    that ended up in the records.
    """
    timebase = model.Timebase()
    timebase.epoch = start_epoch
    timebase.now = model.TIMEBASE_MASK - 1  # 0xFFFFFE: two ticks from the wrap
    capture = model.Capture()
    expected = [timebase.wide]
    arm(capture, mode=0, timestamp=timebase.wide)
    for step in range(1, ticks):
        timebase.tick()
        expected.append(timebase.wide)
        assert capture.record(timebase.wide, step, 0, 0, 0) is True
    return expected, [capture.read(index) >> 32 for index in range(capture.count)]


def test_timestamps_follow_the_timebase_across_the_24_bit_wrap() -> None:
    expected, recorded = _capture_across_timebase(start_epoch=0, ticks=6)
    # The 24-bit counter wraps and carries into the epoch, so the shared value
    # keeps counting up with no offset or discontinuity.
    assert expected == [0x00FFFFFE, 0x00FFFFFF, 0x01000000, 0x01000001, 0x01000002, 0x01000003]
    assert recorded == expected


def test_timestamps_wrap_to_zero_when_the_epoch_wraps_too() -> None:
    expected, recorded = _capture_across_timebase(start_epoch=0xFF, ticks=6)
    assert expected == [0xFFFFFFFE, 0xFFFFFFFF, 0x00000000, 0x00000001, 0x00000002, 0x00000003]
    assert recorded == expected

def test_a_trigger_timestamp_is_the_cycle_the_trigger_fired() -> None:
    capture = model.Capture()
    arm(capture, mode=1, mask=0x01, timestamp=200)
    capture.record(201, 0, 0, 0, 0)
    capture.record(202, 0, 0, 0, 0)
    capture.record(203, 1, 0, 0, 0)
    assert capture.trigger_timestamp == 203
    assert capture.read(0) >> 32 == 203


def test_reset_returns_capture_to_idle() -> None:
    capture = model.Capture()
    arm(capture)
    capture.record(11, 1, 0, 0, 0)
    capture.reset()
    assert not capture.armed and not capture.triggered and not capture.truncated
    assert capture.count == 0
    assert capture.trigger_timestamp == 0
    assert capture.reason == Reason.RESET
    assert capture.read(0) == 0
