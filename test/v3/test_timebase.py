"""The RTL timebase matches the model cycle for cycle, including the wrap."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from protocol_emulator.v3 import model


@cocotb.test()
async def test_v3_timebase_differential(dut):
    """Run 2000 cycles from reset and compare against the model every edge."""
    cocotb.start_soon(Clock(dut.clk, 20.834, units="ns").start())
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    base = model.Timebase()
    for cycle in range(2000):
        await RisingEdge(dut.clk)
        base.tick()
        assert int(dut.now.value) == base.now, (
            f"cycle {cycle}: RTL now={int(dut.now.value)} model now={base.now}"
        )
        assert int(dut.epoch.value) == base.epoch
