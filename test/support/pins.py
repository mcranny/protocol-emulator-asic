"""Simulation helpers for pins."""

from cocotb.triggers import ClockCycles, RisingEdge, Timer


class Host:
    def __init__(self, dut, half_ns=500):
        self.dut = dut
        self.half = half_ns

    async def exchange(self, cmd=0, addr=0, payload=0, bits=40):
        d = self.dut
        word = cmd << 32 | addr << 24 | payload
        d.ui_in.value = 2
        await Timer(250, unit="ns")
        d.ui_in.value = 0
        await Timer(250, unit="ns")
        response = 0
        for index in range(bits):
            bit = (word >> (39 - index)) & 1 if index < 40 else 0
            d.ui_in.value = bit << 2
            await Timer(self.half, unit="ns")
            d.ui_in.value = bit << 2 | 1
            response = response << 1 | (int(d.uo_out.value) & 1)
            await Timer(self.half, unit="ns")
            d.ui_in.value = bit << 2
        await Timer(250, unit="ns")
        d.ui_in.value = 2
        await Timer(250, unit="ns")
        return response

    async def request(self, cmd, addr=0, payload=0, reject=False):
        await self.exchange(cmd, addr, payload)
        response = await self.exchange()
        assert response >> 32 == cmd | (0x80 if reject else 0), hex(response)
        assert response >> 24 & 255 == addr
        return response & 0xFFFFFF

    async def load(self, words):
        for addr, word in enumerate(words):
            assert await self.request(1, addr, word) == word
        for addr, word in enumerate(words):
            assert await self.request(2, addr) == word


async def reset(dut):
    dut.ena.value = 1
    dut.ui_in.value = 2
    dut.uio_in.value = 0xFF
    dut.rst_n.value = 0
    await ClockCycles(dut.clk, 10)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 10)


async def capture(dut, wave):
    while True:
        await RisingEdge(dut.clk)
        await Timer(1, unit="ns")
        oe = int(dut.uio_oe.value) & 1
        value = int(dut.uio_out.value) & 1 if oe else 1
        wave.append((value, oe))


def decode_uart(wave, expected, period):
    """Compare complete bit cells and decode bytes independently of the ISA."""
    starts, cursor = [], 1
    for byte in expected:
        while cursor < len(wave) and not (
            wave[cursor - 1][0] == 1 and wave[cursor][0] == 0
        ):
            cursor += 1
        assert cursor + 10 * period <= len(wave), "missing or truncated UART frame"
        starts.append(cursor)
        bits = []
        for bit in range(10):
            cell = wave[cursor + bit * period : cursor + (bit + 1) * period]
            value = cell[period // 2][0]
            assert all(pair == (value, 1) for pair in cell), (
                "unstable bit cell",
                cursor,
                bit,
            )
            bits.append(value)
        assert bits[0] == 0 and bits[9] == 1
        assert sum(bits[bit + 1] << bit for bit in range(8)) == byte
        cursor += 10 * period
    assert all(b - a == 10 * period for a, b in zip(starts, starts[1:])), starts
    return starts
