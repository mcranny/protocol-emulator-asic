"""V3 host SPI: 48-clock framing, rejection, synchronization and response timing.

A cocotb SPI controller (mode 0, MSB first) clocks frames of any bit count
into ``v3_host_spi``. Expected frames and replies come from the Python codec
``protocol_emulator.v3.host``, never from the RTL.
"""

import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, Timer
from protocol_emulator.v3.host import (
    COMMANDS,
    FRAME_BITS,
    REJECT_BIT,
    VERSION_WORD,
    Frame,
    pack,
    unpack,
)

CLOCK_NS = 40
#: System clocks per SCK phase. The RTL needs at least 3 from falling edge to
#: the next rising edge; 4 leaves margin.
HALF = 4
#: System clocks between CS_n falling and the first SCK edge, and between the
#: last SCK edge and CS_n rising.
GUARD = 4
#: System clocks CS_n stays high after a frame; the RTL needs at least 4.
IDLE = 6

REJECT_WORD = pack(REJECT_BIT, 0, 0)


def reply_for(frame: Frame) -> int:
    """Reply word the stand-in core returns for an accepted command."""
    if frame.cmd == COMMANDS["VERSION"]:
        return pack(frame.cmd, frame.addr, VERSION_WORD)
    return pack(frame.cmd, frame.addr, frame.payload ^ 0xA5A5A5A5)


class Bench:
    """Clock, stand-in core, event monitor and SPI controller for one DUT."""

    def __init__(self, dut):
        self.dut = dut
        self.events = []  # ("cmd", Frame) or ("error", None), one per clock seen
        self.tick_count = 0
        self.wide_pulses = 0

    async def start(self):
        dut = self.dut
        dut.sck.value = 0
        dut.cs_n.value = 1
        dut.mosi.value = 0
        dut.ena.value = 1
        dut.reply.value = 0
        dut.rst_n.value = 0
        cocotb.start_soon(Clock(dut.clk, CLOCK_NS, "ns").start())
        for _ in range(3):
            await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        await FallingEdge(dut.clk)
        cocotb.start_soon(self._monitor())

    async def _monitor(self):
        """Present the stand-in core's reply and record every event pulse."""
        dut = self.dut
        previous = (0, 0)
        while True:
            await FallingEdge(dut.clk)
            self.tick_count += 1
            valid = int(dut.cmd_valid.value)
            error = int(dut.frame_error.value)
            fields = Frame(
                int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value)
            )
            dut.reply.value = reply_for(fields)
            assert not (valid and error), "cmd_valid and frame_error together"
            if valid:
                self.events.append(("cmd", fields))
            if error:
                self.events.append(("error", None))
            if (valid and previous[0]) or (error and previous[1]):
                self.wide_pulses += 1
            previous = (valid, error)

    async def ticks(self, count):
        for _ in range(count):
            await FallingEdge(self.dut.clk)

    async def frame(self, bits, nbits):
        """Clock ``nbits`` bits of ``bits`` (MSB first) and return MISO samples.

        ``bits`` is a list of 0/1 values with at least ``nbits`` entries. MISO
        is sampled just before each SCK rising edge.
        """
        dut = self.dut
        dut.cs_n.value = 0
        await self.ticks(GUARD)
        miso = []
        for index in range(nbits):
            dut.mosi.value = bits[index]
            await self.ticks(2)
            miso.append(int(dut.miso.value))
            dut.sck.value = 1
            await self.ticks(HALF)
            dut.sck.value = 0
            await self.ticks(HALF - 2)
        await self.ticks(GUARD)
        dut.cs_n.value = 1
        await self.ticks(IDLE)
        return miso

    async def send(self, value, nbits=FRAME_BITS, extra=0):
        """Clock a 48-bit ``value`` truncated or padded to ``nbits`` bits."""
        bits = [(value >> (FRAME_BITS - 1 - i)) & 1 for i in range(FRAME_BITS)]
        bits += [(extra >> i) & 1 for i in range(max(0, nbits - FRAME_BITS))]
        return await self.frame(bits, nbits)

    def take_events(self):
        events, self.events = self.events, []
        return events


def word(miso):
    value = 0
    for bit in miso:
        value = (value << 1) | bit
    return value


async def make_bench(dut):
    bench = Bench(dut)
    await bench.start()
    return bench


@cocotb.test()
async def test_exact_48_accepted(dut):
    bench = await make_bench(dut)
    sent = Frame(COMMANDS["WRITE"], 0x12, 0xDEADBEEF)
    miso = await bench.send(pack(*sent))
    assert bench.take_events() == [("cmd", sent)]
    assert bench.wide_pulses == 0
    assert word(miso) == 0, "nothing was owed to the first frame"


@cocotb.test()
async def test_response_appears_on_following_frame(dut):
    bench = await make_bench(dut)
    first = Frame(COMMANDS["VERSION"], 0x00, 0)
    second = Frame(COMMANDS["READ"], 0x34, 0x01020304)
    third = Frame(COMMANDS["NOP"], 0x00, 0)

    miso1 = await bench.send(pack(*first))
    assert word(miso1) == 0
    miso2 = await bench.send(pack(*second))
    assert word(miso2) == pack(first.cmd, first.addr, VERSION_WORD)
    assert unpack(word(miso2)).payload == VERSION_WORD
    assert word(miso2) != reply_for(second), "a frame must not answer itself"
    miso3 = await bench.send(pack(*third))
    assert word(miso3) == reply_for(second)
    assert [e[1] for e in bench.take_events()] == [first, second, third]


@cocotb.test()
async def test_47_clocks_rejected(dut):
    bench = await make_bench(dut)
    good = Frame(COMMANDS["WRITE"], 0x01, 0x11111111)
    await bench.send(pack(*good))
    bench.take_events()
    cmd_before = (int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value))

    await bench.send(pack(COMMANDS["START"], 0xFF, 0xFFFFFFFF), nbits=47)
    assert bench.take_events() == [("error", None)]
    assert (int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value)) == cmd_before

    next_good = Frame(COMMANDS["READ"], 0x02, 0x22222222)
    miso = await bench.send(pack(*next_good))
    assert word(miso) == REJECT_WORD, "rejection is reported on the following frame"
    assert bench.take_events() == [("cmd", next_good)]
    miso = await bench.send(pack(COMMANDS["NOP"], 0, 0))
    assert word(miso) == reply_for(next_good)
    bench.take_events()


@cocotb.test()
async def test_49_clocks_rejected(dut):
    bench = await make_bench(dut)
    cmd_before = (int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value))
    # The first 48 bits form a legal frame; the 49th must still void it.
    for extra in (0, 1):
        await bench.send(
            pack(COMMANDS["STOP"], 0x7F, 0x12345678), nbits=FRAME_BITS + 1, extra=extra
        )
        assert bench.take_events() == [("error", None)]
        assert (int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value)) == cmd_before
        miso = await bench.send(pack(COMMANDS["NOP"], 0, 0))
        assert word(miso) == REJECT_WORD
        assert bench.take_events() == [("cmd", Frame(COMMANDS["NOP"], 0, 0))]
    assert bench.wide_pulses == 0


@cocotb.test()
async def test_other_counts_rejected(dut):
    bench = await make_bench(dut)
    for nbits in (0, 1, 8, 24, 46, 50, 64, 96, 100, 130):
        await bench.send(pack(COMMANDS["WRITE"], 0x55, 0xAAAAAAAA), nbits=nbits, extra=-1)
        assert bench.take_events() == [("error", None)], nbits
    miso = await bench.send(pack(COMMANDS["NOP"], 0, 0))
    assert word(miso) == REJECT_WORD
    assert bench.take_events() == [("cmd", Frame(COMMANDS["NOP"], 0, 0))]


@cocotb.test()
async def test_synchronizer_latency(dut):
    """The accepted command appears three clocks after CS_n is driven high."""
    bench = await make_bench(dut)
    value = pack(COMMANDS["CONFIG"], 0x03, 0x00C0FFEE)
    bits = [(value >> (FRAME_BITS - 1 - i)) & 1 for i in range(FRAME_BITS)]
    dut.cs_n.value = 0
    await bench.ticks(GUARD)
    for bit in bits:
        dut.mosi.value = bit
        await bench.ticks(2)
        dut.sck.value = 1
        await bench.ticks(HALF)
        dut.sck.value = 0
        await bench.ticks(HALF - 2)
    await bench.ticks(GUARD)
    bench.take_events()
    dut.cs_n.value = 1
    for tick in range(1, 3):
        await bench.ticks(1)
        assert int(dut.cmd_valid.value) == 0, f"early at tick {tick}"
    await bench.ticks(1)
    assert int(dut.cmd_valid.value) == 1
    assert (int(dut.cmd.value), int(dut.addr.value), int(dut.payload.value)) == tuple(
        unpack(value)
    )
    await bench.ticks(1)
    assert int(dut.cmd_valid.value) == 0
    await bench.ticks(IDLE)


@cocotb.test()
async def test_inputs_are_synchronized(dut):
    """Edges on SCK, CS_n and MOSI take effect two clocks after they are driven."""
    bench = await make_bench(dut)
    # Take one frame's worth of state: select, then check internal sync flops.
    dut.cs_n.value = 0
    dut.mosi.value = 1
    dut.sck.value = 1
    await bench.ticks(1)
    # Driven at the previous falling edge, sampled by the first flop on the
    # rising edge in between, but not yet by the second.
    assert int(dut.cs_sync.value) == 0b10
    assert int(dut.sck_sync.value) == 0b01
    assert int(dut.mosi_sync.value) == 0b01
    await bench.ticks(1)
    assert int(dut.cs_sync.value) == 0b00
    assert int(dut.sck_sync.value) == 0b11
    assert int(dut.mosi_sync.value) == 0b11
    assert int(dut.cs_prev.value) == 1
    await bench.ticks(1)
    assert int(dut.cs_prev.value) == 0
    dut.sck.value = 0
    dut.cs_n.value = 1
    await bench.ticks(IDLE)
    bench.take_events()


@cocotb.test()
async def test_miso_gated_by_raw_cs(dut):
    """MISO releases and drives with the raw CS_n, not the synchronized one."""
    bench = await make_bench(dut)
    await bench.send(pack(COMMANDS["NOP"], 0, 0), nbits=FRAME_BITS - 1)
    bench.take_events()
    # The reject word is owed; its first bit is 1.
    dut.cs_n.value = 0
    await Timer(1, "ns")
    assert int(dut.miso.value) == 1, "MISO follows raw CS_n low with no clock"
    assert int(dut.cs_sync.value) == 0b11
    await bench.ticks(3)
    assert int(dut.cs_sync.value) == 0b00
    dut.cs_n.value = 1
    await Timer(1, "ns")
    assert int(dut.cs_sync.value) == 0b00, "the synchronizer has not seen the deselect"
    assert int(dut.miso.value) == 0, "MISO released with no clock after CS_n high"
    await bench.ticks(IDLE)
    # That empty selection was itself a rejected frame.
    assert bench.take_events() == [("error", None)]


@cocotb.test()
async def test_reset_and_disable(dut):
    bench = await make_bench(dut)
    await bench.send(pack(COMMANDS["VERSION"], 0, 0))
    bench.take_events()
    dut.ena.value = 0
    await bench.ticks(2)
    dut.cs_n.value = 0
    await Timer(1, "ns")
    assert int(dut.miso.value) == 0, "disabled module drives nothing"
    dut.cs_n.value = 1
    dut.ena.value = 1
    await bench.ticks(IDLE)
    miso = await bench.send(pack(COMMANDS["NOP"], 0, 0))
    assert word(miso) == 0, "disable cleared the owed response"
    bench.take_events()

    await bench.send(pack(COMMANDS["VERSION"], 0, 0))
    dut.rst_n.value = 0
    await bench.ticks(2)
    dut.rst_n.value = 1
    await bench.ticks(2)
    bench.take_events()
    miso = await bench.send(pack(COMMANDS["NOP"], 0, 0))
    assert word(miso) == 0, "reset cleared the owed response"


@cocotb.test()
async def test_random_frames_match_codec(dut):
    bench = await make_bench(dut)
    rng = random.Random(0x48)
    codes = list(COMMANDS.values())
    bad_counts = [0, 1, 2, 7, 46, 47, 49, 50, 64, 96, 97]
    owed = 0
    accepted = rejected = 0
    for _ in range(250):
        if rng.random() < 0.7:
            nbits = FRAME_BITS
        else:
            nbits = rng.choice(bad_counts)
        cmd = rng.choice(codes) if rng.random() < 0.8 else rng.randrange(256)
        sent = Frame(cmd, rng.randrange(256), rng.getrandbits(32))
        value = pack(*sent)
        assert unpack(value) == sent
        extra = rng.getrandbits(max(1, nbits))
        miso = await bench.send(value, nbits=nbits, extra=extra)
        # MISO shifts out the response owed from the previous frame, MSB first.
        compared = min(nbits, FRAME_BITS)
        assert word(miso[:compared]) == owed >> (FRAME_BITS - compared)
        events = bench.take_events()
        if nbits == FRAME_BITS:
            assert events == [("cmd", sent)]
            owed = reply_for(sent)
            accepted += 1
        else:
            assert events == [("error", None)]
            owed = REJECT_WORD
            rejected += 1
    assert bench.wide_pulses == 0
    assert accepted > 100 and rejected > 30
    dut._log.info("random frames: %d accepted, %d rejected", accepted, rejected)
