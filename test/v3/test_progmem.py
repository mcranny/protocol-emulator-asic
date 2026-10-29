"""The program store honours the one-cycle registered read contract."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20.833, "ns").start())
    dut.rst_n.value = 0
    dut.fetch_en.value = 0
    dut.write_en.value = 0
    dut.invalidate.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def write(dut, addr, word):
    dut.write_en.value = 1
    dut.write_addr.value = addr
    dut.write_data.value = word
    await RisingEdge(dut.clk)
    dut.write_en.value = 0


@cocotb.test()
async def test_v3_progmem_read_is_one_cycle_late(dut):
    await reset(dut)
    await write(dut, 7, 0xDEADBEEF)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 7
    await RisingEdge(dut.clk)
    # The address was presented on the edge just passed; data is not valid yet.
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0xDEADBEEF


@cocotb.test()
async def test_v3_progmem_back_to_back_reads_stream(dut):
    await reset(dut)
    for addr in range(8):
        await write(dut, addr, 0x1000 + addr)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 0
    await RisingEdge(dut.clk)
    for addr in range(1, 8):
        dut.fetch_addr.value = addr
        await RisingEdge(dut.clk)
        assert int(dut.fetch_data.value) == 0x1000 + addr - 1


@cocotb.test()
async def test_v3_progmem_image_is_incomplete_until_every_word_is_written(dut):
    await reset(dut)
    for addr in range(255):
        await write(dut, addr, 0)
        assert int(dut.image_complete.value) == 0
    await write(dut, 255, 0)
    await RisingEdge(dut.clk)
    assert int(dut.image_complete.value) == 1


@cocotb.test()
async def test_v3_progmem_fetching_an_unwritten_word_is_not_valid(dut):
    await reset(dut)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 42
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 0


@cocotb.test()
async def test_v3_progmem_fetch_valid_holds_until_the_next_read(dut):
    await reset(dut)
    await write(dut, 5, 0)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 5
    await RisingEdge(dut.clk)
    dut.fetch_en.value = 0
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 1
    dut.invalidate.value = 1
    await RisingEdge(dut.clk)
    dut.invalidate.value = 0
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 1
    await write(dut, 7, 0)
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 1
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 9
    await RisingEdge(dut.clk)
    dut.fetch_en.value = 0
    await write(dut, 9, 0)
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 0


@cocotb.test()
async def test_v3_progmem_invalidate_clears_the_bitmap(dut):
    await reset(dut)
    for addr in range(256):
        await write(dut, addr, 0)
    await RisingEdge(dut.clk)
    assert int(dut.image_complete.value) == 1
    dut.invalidate.value = 1
    await RisingEdge(dut.clk)
    dut.invalidate.value = 0
    await RisingEdge(dut.clk)
    assert int(dut.image_complete.value) == 0


@cocotb.test()
async def test_v3_progmem_address_boundaries(dut):
    await reset(dut)
    await write(dut, 0, 0xFFFFFFFF)
    await write(dut, 255, 0x00000001)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 0
    await RisingEdge(dut.clk)
    dut.fetch_addr.value = 255
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0xFFFFFFFF
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0x00000001
