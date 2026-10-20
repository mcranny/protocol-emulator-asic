"""Three consecutive agreeing synchronized samples qualify a filtered change."""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, Timer


class InputFilterReference:
    """Independent reference for the three-sample qualifier."""

    def __init__(self):
        self.history = [0, 0]
        self.value = 0

    def reset(self):
        self.history = [0, 0]
        self.value = 0

    def tick(self, samples):
        for bit in range(8):
            levels = [value >> bit & 1 for value in (*self.history, samples)]
            if all(levels):
                self.value |= 1 << bit
            elif not any(levels):
                self.value &= ~(1 << bit)
        self.history = [self.history[1], samples]


async def release_reset(dut):
    dut.rst_n.value = 0
    dut.samples.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)
    return 0


@cocotb.test()
async def test_v3_input_filter_requires_three_samples_on_every_pin(dut):
    """Match the reference on scripted and random synchronized samples."""
    cocotb.start_soon(Clock(dut.clk, 20834, units="ps").start())
    ref = InputFilterReference()
    last_sample = await release_reset(dut)
    ref.tick(last_sample)
    await Timer(1, units="step")
    assert int(dut.filtered.value) == ref.value == 0

    rng = random.Random(20924)
    sequence = [255] * 5 + [0, 0, 255] + [rng.randrange(256) for _ in range(1000)]
    for sample in sequence:
        dut.samples.value = sample
        await RisingEdge(dut.clk)
        await Timer(1, units="step")
        ref.tick(sample)
        assert int(dut.filtered.value) == ref.value, (
            f"sample={sample}: RTL={int(dut.filtered.value)} ref={ref.value}"
        )


@cocotb.test()
async def test_v3_input_filter_reset_clears_state(dut):
    cocotb.start_soon(Clock(dut.clk, 20834, units="ps").start())
    ref = InputFilterReference()
    last_sample = await release_reset(dut)
    ref.tick(last_sample)
    dut.samples.value = 255
    for _ in range(8):
        await RisingEdge(dut.clk)
        await Timer(1, units="step")
        ref.tick(255)
    assert int(dut.filtered.value) == ref.value == 255

    dut.rst_n.value = 0
    await RisingEdge(dut.clk)
    await Timer(1, units="step")
    ref.reset()
    assert int(dut.filtered.value) == ref.value == 0

    dut.rst_n.value = 1
    dut.samples.value = 0
    await RisingEdge(dut.clk)
    await Timer(1, units="step")
    ref.tick(0)
    assert int(dut.filtered.value) == ref.value == 0
