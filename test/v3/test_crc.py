"""The RTL CRC unit matches model.Crc and the published vectors, bit by bit.

Every message bit is its own ``shift`` strobe, which is how the engine drives
the unit: one CRCBIT instruction per bit. The expected check values are
written out here, independent of the model, and the register is also compared
with the model after every single bit.
"""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from protocol_emulator.v3 import model


class CrcHarness:
    """Drives the strobes of ``v3_crc`` and mirrors every step into the model."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.model = model.Crc()

    async def reset(self) -> None:
        cocotb.start_soon(Clock(self.dut.clk, 20, "ns").start())
        self.idle()
        self.dut.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.model = model.Crc()
        self.check("after reset")

    def idle(self) -> None:
        self.dut.configure.value = 0
        self.dut.load.value = 0
        self.dut.shift.value = 0
        self.dut.poly.value = 0
        self.dut.msb_first.value = 0
        self.dut.seed.value = 0
        self.dut.bit_in.value = 0

    @property
    def value(self) -> int:
        return int(self.dut.value.value)

    def check(self, where: str) -> None:
        assert self.value == self.model.value, (
            f"{where}: RTL {self.value:#06x} model {self.model.value:#06x}"
        )

    async def strobe(self, **inputs: int) -> None:
        """Hold the named inputs for exactly one clock edge, then go idle."""
        for name, value in inputs.items():
            getattr(self.dut, name).value = value
        await RisingEdge(self.dut.clk)
        self.idle()
        # The register output is combinational from state, so let the edge settle.
        await RisingEdge(self.dut.clk)

    async def configure(self, poly: int, msb_first: bool) -> None:
        await self.strobe(configure=1, poly=poly, msb_first=int(msb_first))
        self.model.configure(poly=poly, msb_first=msb_first)
        self.check("configure")

    async def load(self, seed: int) -> None:
        await self.strobe(load=1, seed=seed)
        self.model.load(seed)
        self.check("load")

    async def shift(self, bit: int) -> None:
        await self.strobe(shift=1, bit_in=bit)
        self.model.shift(bit)
        self.check("shift")

    async def crc_over(self, data: bytes, poly: int, seed: int, msb_first: bool) -> int:
        await self.configure(poly, msb_first)
        await self.load(seed)
        for byte in data:
            order = range(7, -1, -1) if msb_first else range(8)
            for index in order:
                await self.shift((byte >> index) & 1)
        return self.value


@cocotb.test()
async def test_v3_crc_ccitt_vector(dut):
    """CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, MSB first, "123456789"."""
    crc = CrcHarness(dut)
    await crc.reset()
    assert await crc.crc_over(b"123456789", 0x1021, 0xFFFF, True) == 0x29B1


@cocotb.test()
async def test_v3_crc_arc_vector(dut):
    """CRC-16/ARC: reflected poly 0xA001, init 0, LSB first, "123456789"."""
    crc = CrcHarness(dut)
    await crc.reset()
    assert await crc.crc_over(b"123456789", 0xA001, 0x0000, False) == 0xBB3D


@cocotb.test()
async def test_v3_crc_usb_crc5_residual(dut):
    """USB CRC5: stays in five bits and a message plus its CRC leaves a constant residual."""
    crc = CrcHarness(dut)
    await crc.reset()

    async def crc5(bits: list[int]) -> int:
        await crc.configure(poly=0x0014, msb_first=False)  # x^5+x^2+1, reflected
        await crc.load(0x1F)
        for bit in bits:
            await crc.shift(bit)
        assert crc.value <= 0x1F, "a 5-bit polynomial must not leave five bits"
        return crc.value

    residuals = set()
    for token in (0x0015, 0x0000, 0x07FF, 0x03A5):
        message = [(token >> index) & 1 for index in range(11)]
        check = await crc5(message) ^ 0x1F
        appended = message + [(check >> index) & 1 for index in range(5)]
        residuals.add(await crc5(appended))
    assert len(residuals) == 1, f"residual is not constant: {residuals}"


@cocotb.test()
async def test_v3_crc_reset_state(dut):
    """Reset leaves the register at zero, MSB first, with a zero polynomial."""
    crc = CrcHarness(dut)
    await crc.reset()
    assert crc.value == 0
    # A zero polynomial and MSB-first direction mean a shifted 1 just moves up.
    await crc.shift(1)
    assert crc.value == 0
    await crc.load(0x0001)
    await crc.shift(0)
    assert crc.value == 0x0002


@cocotb.test()
async def test_v3_crc_configure_does_not_disturb_the_state(dut):
    """Reconfiguring, even to the other direction, leaves the register as it was."""
    crc = CrcHarness(dut)
    await crc.reset()
    await crc.configure(poly=0x1021, msb_first=True)
    await crc.load(0xFFFF)
    await crc.shift(1)
    before = crc.value
    await crc.configure(poly=0x1021, msb_first=True)
    assert crc.value == before
    await crc.configure(poly=0xA001, msb_first=False)
    assert crc.value == before


@cocotb.test()
async def test_v3_crc_shift_uses_the_latched_configuration(dut):
    """The polynomial and direction persist across shifts until reconfigured."""
    crc = CrcHarness(dut)
    await crc.reset()
    await crc.configure(poly=0x8005, msb_first=True)
    await crc.load(0xFFFF)
    for _ in range(64):
        await crc.shift(1)
    await crc.configure(poly=0x8005, msb_first=False)
    for _ in range(64):
        await crc.shift(1)


@cocotb.test()
async def test_v3_crc_idle_cycles_hold_the_state(dut):
    """Cycles with no strobe, and a bit on the line with no shift, change nothing."""
    crc = CrcHarness(dut)
    await crc.reset()
    await crc.configure(poly=0x1021, msb_first=True)
    await crc.load(0xFFFF)
    await crc.shift(0)
    held = crc.value
    dut.bit_in.value = 1
    for _ in range(8):
        await RisingEdge(dut.clk)
        assert crc.value == held
    dut.bit_in.value = 0


@cocotb.test()
async def test_v3_crc_random_differential(dut):
    """Random configure, load and shift sequences agree with the model after every operation."""
    crc = CrcHarness(dut)
    await crc.reset()
    rng = random.Random(0x3C2C)
    for _ in range(1500):
        choice = rng.random()
        if choice < 0.1:
            await crc.configure(rng.getrandbits(16), bool(rng.getrandbits(1)))
        elif choice < 0.2:
            await crc.load(rng.getrandbits(16))
        else:
            await crc.shift(rng.getrandbits(1))


@cocotb.test()
async def test_v3_crc_load_wins_over_shift(dut):
    """With load and shift asserted together, the seed lands and nothing is shifted."""
    crc = CrcHarness(dut)
    await crc.reset()
    await crc.configure(poly=0x1021, msb_first=True)
    await crc.load(0x1234)
    await crc.strobe(load=1, seed=0xBEEF, shift=1, bit_in=1)
    assert crc.value == 0xBEEF
