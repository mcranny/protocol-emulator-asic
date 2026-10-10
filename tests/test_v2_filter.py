import random

import pytest

from protocol_emulator.v2.model import Device, InputFilter
from protocol_emulator.v2.isa import assemble


def test_filter_requires_three_samples_on_every_pin():
    filter_model = InputFilter()
    rng = random.Random(20924)
    previous = [0] * 8
    count = [0] * 8
    expected = 0
    for sample in [255] * 5 + [0, 0, 255] + [rng.randrange(256) for _ in range(1000)]:
        assert filter_model.tick(sample) == expected
        for pin in range(8):
            value = sample >> pin & 1
            count[pin] = count[pin] + 1 if value == previous[pin] else 1
            previous[pin] = value
            if count[pin] >= 3:
                expected = (expected & ~(1 << pin)) | value << pin


def test_filter_configuration_and_reset_lifecycle():
    device = Device(capacity=64)
    device.configure_filter(3)
    device.engines[0].load(assemble('JMP 0', capacity=64))
    device.start(1)
    with pytest.raises(ValueError, match='stopped'):
        device.configure_filter(0)
    device.tick(255, enable=False)
    assert device.filter_mask == 3
    device.tick(255, reset=True)
    assert device.filter_mask == 0 and device.input_filter.value == 0


@pytest.mark.parametrize('mask', [True, -1, 256, 1.5, None])
def test_invalid_filter_mask(mask):
    with pytest.raises(ValueError):
        Device().configure_filter(mask)
