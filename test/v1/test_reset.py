"""V1 four-state startup, reset, and state-loss regression."""

import cocotb
from support.reset import exercise


@cocotb.test()
async def test_v1_reset_and_state_loss(dut):
    await exercise(dut, 1)
