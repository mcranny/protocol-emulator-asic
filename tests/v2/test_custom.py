import pytest
from protocol_emulator.v2.custom_protocol import decode, receive, transmit
from protocol_emulator.v2.model import Device


@pytest.mark.parametrize(
    "value",
    range(16),
    ids=[
        "value=0",
        "value=1",
        "value=2",
        "value=3",
        "value=4",
        "value=5",
        "value=6",
        "value=7",
        "value=8",
        "value=9",
        "value=10",
        "value=11",
        "value=12",
        "value=13",
        "value=14",
        "value=15",
    ],
)
@pytest.mark.parametrize(
    "synchronization", [2, 3], ids=["synchronization=2", "synchronization=3"]
)
def test_custom_protocol_uses_frozen_instructions(value, synchronization):
    device = Device(capacity=64)
    device.configure([1, 0])
    device.engines[0].load(transmit(value))
    device.engines[1].load(receive())
    device.start(3)
    pipe, events = [0] * synchronization, []
    for cycle in range(400):
        out, oe = device.pins
        pipe.append(out & oe & 1)
        out, oe = device.tick(pipe.pop(0))
        level = out & oe & 1
        if not events or events[-1][1] != level:
            events.append((cycle, level))
    assert decode(events) == value
    assert list(device.engines[1].rx) == [(value, 0x50)]
    assert all(not e.running and not e.errors for e in device.engines)


def test_decoder_rejects_wrong_width():
    with pytest.raises(ValueError):
        decode([(0, 1), (39, 0)])
