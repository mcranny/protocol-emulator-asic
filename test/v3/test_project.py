"""Full-chip V3 differential.

Loads a real program into each engine's SRAM through the host SPI port,
configures disjoint pin ownership, starts both engines in one frame, then
compares every ``uio`` transition of the integrated chip against a composed
Python model for a long run.

The oracle is built only from the published blocks in
``host/protocol_emulator/v3``: a :class:`~protocol_emulator.v3.model.Timebase`,
eight shared :class:`~protocol_emulator.v3.model.PinCell` instances, two
:class:`~protocol_emulator.v3.model.Engine` instances over those pins (sharing
one :class:`~protocol_emulator.v3.model.Crc`), and the host-frame codec in
``protocol_emulator.v3.host``. Each per-block model is already proven equal to
its RTL block by the block-level differential suites; this test checks that the
top-level wiring (shared timebase, disjoint-ownership commit routing, the pin
drives reaching ``uio_out``/``uio_oe``, and the running/error status outputs)
composes those blocks exactly as the model does.

Alignment follows the block-level engine harness (``test/support/v3_engine.py``):
per clock, observe the pin inputs, step engine 0 then engine 1 (fixed order, so
the shared CRC is deterministic), tick the pin cells, then compare after the
rising edge. The external pin inputs are held at zero for the whole run, so the
synchronized, filtered engine-visible level is zero in both the RTL and the
model regardless of the filter/synchronizer ordering, and the comparison is of
the output datapath the engines drive.

Cocotb 1.9.2: ``Clock`` is given a positional unit string, and both ``MODULE``
and ``COCOTB_TEST_MODULES`` are set by the Makefile.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from protocol_emulator.v3 import PIN_COUNT, model
from protocol_emulator.v3.host import (
    COMMANDS,
    ENGINE_BIT,
    FRAME_BITS,
    REJECT_BIT,
    Frame,
    pack,
    unpack,
)
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.model import TIMEBASE_MASK

#: Clock period in nanoseconds (48 MHz). Positional unit string for 1.9.2.
CLOCK_NS = 20.834

#: System clocks per SCK half-period. The host SPI 2FF synchronizer needs at
#: least three system clocks in each SCK phase; this leaves a clock of margin.
HALF = 4
#: System clocks between CS_n falling and the first SCK edge, and after the last.
GUARD = 4
#: System clocks CS_n is held high between frames (the RTL needs at least four).
IDLE = 6

#: Comparison length after both engines start. Override for quick runs.
COMPARE_CYCLES = int(os.environ.get("V3_COMPARE_CYCLES", "200000"))

#: Disjoint pin ownership: engine 0 owns uio[3:0], engine 1 owns uio[7:4].
OWNER0 = 0x0F
OWNER1 = 0xF0

# Looping programs: each drives its own pins with timed, scheduled commits so
# the chip produces a continuous stream of uio transitions. Neither uses the
# CRC, the queues or input-dependent stalls, so the two engines never contend
# for the shared CRC and the engine-visible pin levels (held at zero) never
# gate execution.
PROGRAM0 = """
loop0:
    OUT pin=0, level=1, offset=4
    ADVANCE ticks=40
    WAITTIME
    OUT pin=1, level=1, offset=4
    ADVANCE ticks=40
    WAITTIME
    OUT pin=0, level=0, offset=4
    OUT pin=1, level=0, offset=4
    ADVANCE ticks=40
    WAITTIME
    JMP target=loop0
"""

PROGRAM1 = """
loop1:
    OUT pin=4, level=1, offset=3
    ADVANCE ticks=25
    WAITTIME
    OUT pin=5, level=1, offset=3
    ADVANCE ticks=25
    WAITTIME
    OUT pin=4, level=0, offset=3
    OUT pin=5, level=0, offset=3
    ADVANCE ticks=25
    WAITTIME
    JMP target=loop1
"""


def _bits(value: int) -> list:
    """The 48 frame bits, most significant (first transmitted) first."""
    return [(value >> (FRAME_BITS - 1 - i)) & 1 for i in range(FRAME_BITS)]


def _word(samples: list) -> int:
    value = 0
    for bit in samples:
        value = (value << 1) | (bit & 1)
    return value


class Chip:
    """Drives the SPI port of the full chip and reads its status pins."""

    def __init__(self, dut):
        self.dut = dut

    def _drive_spi(self, sck: int, cs_n: int, mosi: int) -> None:
        """Present the three SPI inputs on ui_in, holding the rest low."""
        self.dut.ui_in.value = (sck & 1) | ((cs_n & 1) << 1) | ((mosi & 1) << 2)

    async def _ticks(self, count: int) -> None:
        for _ in range(count):
            await FallingEdge(self.dut.clk)

    async def reset(self) -> None:
        dut = self.dut
        self._drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        dut.ena.value = 1
        dut.rst_n.value = 0
        cocotb.start_soon(Clock(dut.clk, CLOCK_NS, "ns").start())
        for _ in range(4):
            await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        await FallingEdge(dut.clk)

    async def frame(self, value: int, finish: bool = True) -> int:
        """Clock one 48-bit frame, MSB first, returning the 48 MISO samples.

        MISO (``uo_out[0]``) is sampled just before each SCK rising edge, so the
        returned word is the reply the chip owes from the previous frame. When
        ``finish`` is false the trailing idle wait is skipped so the caller can
        watch the chip immediately (used for the START frame).
        """
        dut = self.dut
        bits = _bits(value)
        self._drive_spi(sck=0, cs_n=0, mosi=0)
        await self._ticks(GUARD)
        samples = []
        for bit in bits:
            self._drive_spi(sck=0, cs_n=0, mosi=bit)
            await self._ticks(2)
            samples.append(int(dut.uo_out.value) & 1)
            self._drive_spi(sck=1, cs_n=0, mosi=bit)
            await self._ticks(HALF)
            self._drive_spi(sck=0, cs_n=0, mosi=bit)
            await self._ticks(HALF - 2)
        await self._ticks(GUARD)
        self._drive_spi(sck=0, cs_n=1, mosi=0)
        if finish:
            await self._ticks(IDLE)
        return _word(samples)


def _build_requests(image0, image1):
    """The full setup request list: write both images, configure, start."""
    write0 = COMMANDS["WRITE"]
    write1 = COMMANDS["WRITE"] | ENGINE_BIT
    requests = [Frame(write0, addr, word) for addr, word in enumerate(image0.words)]
    requests += [Frame(write1, addr, word) for addr, word in enumerate(image1.words)]
    requests.append(Frame(COMMANDS["CONFIG"], 0, OWNER0))
    requests.append(Frame(COMMANDS["CONFIG"] | ENGINE_BIT, 0, OWNER1))
    requests.append(Frame(COMMANDS["START"], 0, 0b11))
    return requests


def _check_reply(index, request, reply_word):
    """Raise if the chip rejected or mis-echoed ``request``."""
    reply = unpack(reply_word)
    if reply.cmd & REJECT_BIT:
        raise AssertionError(f"setup frame {index} {request} was rejected by the chip")
    if reply.cmd != request.cmd or reply.addr != request.addr:
        raise AssertionError(
            f"setup frame {index} {request} got a mismatched reply "
            f"(cmd 0x{reply.cmd:02X}, addr {reply.addr})"
        )


@cocotb.test()
async def test_v3_full_chip_differential(dut):
    """Load, start, and compare every uio transition against the model."""
    image0 = assemble(PROGRAM0)
    image1 = assemble(PROGRAM1)

    chip = Chip(dut)
    await chip.reset()

    # Drive the whole setup. The reply to request i rides out on request i+1's
    # MISO (the chip answers one frame late); the first frame's MISO is stale
    # and discarded. The START frame carries the last CONFIG's reply, and is
    # clocked without a trailing idle so the engines can be caught starting.
    requests = _build_requests(image0, image1)
    previous_miso = await chip.frame(pack(*requests[0]))
    for index in range(1, len(requests)):
        is_start = index == len(requests) - 1
        miso = await chip.frame(pack(*requests[index]), finish=not is_start)
        _check_reply(index - 1, requests[index - 1], miso)
        previous_miso = miso

    # The engines must be idle until START takes effect.
    assert int(dut.uio_oe.value) == 0, "a pin was driven before START"

    # Catch the exact cycle both engines latch running. START is applied a few
    # cycles after its CS_n rises (through the SPI synchronizer); watch every
    # edge so the model can be aligned to the same timebase value.
    now_post = None
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        status = int(dut.uo_out.value)
        if (status >> 1) & 1:
            assert (status >> 2) & 1, "the two engines did not start on one cycle"
            now_post = int(dut.u_timebase.now.value)
            break
    assert now_post is not None, "the engines never started after START"

    # At the detection edge the timebase has already advanced past the START
    # cycle, so the cursor the engines latched is now_post - 1 and the first
    # instruction executes at now_post.
    start_now = (now_post - 1) & TIMEBASE_MASK
    model_now = now_post

    pins = [model.PinCell(open_drain=False) for _ in range(PIN_COUNT)]
    crc = model.Crc()
    engine0 = model.Engine(pins=pins, owner=OWNER0, crc=crc)
    engine1 = model.Engine(pins=pins, owner=OWNER1, crc=crc)
    engine0.load(image0)
    engine1.load(image1)
    engine0.start(start_now)
    engine1.start(start_now)

    def model_uio():
        out = oe = 0
        for i, cell in enumerate(pins):
            value, enable = cell.drive()
            out |= (value & 1) << i
            oe |= (enable & 1) << i
        return out, oe

    for cycle in range(COMPARE_CYCLES):
        await FallingEdge(dut.clk)
        # Inputs are held idle: SPI deselected, external pins low.
        chip._drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        for cell in pins:
            cell.observe(0, model_now)
        await ReadOnly()

        await RisingEdge(dut.clk)
        engine0.step(model_now)
        engine1.step(model_now)
        for cell in pins:
            cell.tick(model_now)

        await ReadOnly()
        # The timebase must advance exactly one tick per clock, confirming the
        # model stayed aligned with the DUT's internal counter.
        dut_now = int(dut.u_timebase.now.value)
        assert dut_now == (model_now + 1) & TIMEBASE_MASK, (
            f"cycle {cycle}: timebase diverged, dut now={dut_now} "
            f"model now+1={(model_now + 1) & TIMEBASE_MASK}"
        )

        dut_out = int(dut.uio_out.value) & 0xFF
        dut_oe = int(dut.uio_oe.value) & 0xFF
        m_out, m_oe = model_uio()
        if (dut_out, dut_oe) != (m_out, m_oe):
            for i in range(PIN_COUNT):
                dv, do = (dut_out >> i) & 1, (dut_oe >> i) & 1
                mv, mo = (m_out >> i) & 1, (m_oe >> i) & 1
                if (dv, do) != (mv, mo):
                    raise AssertionError(
                        f"cycle {cycle} (now {model_now}): pin {i} diverged, "
                        f"dut (out={dv}, oe={do}) model (out={mv}, oe={mo})"
                    )

        # Status pins: MISO released (CS_n high), both engines running, no fault.
        status = int(dut.uo_out.value)
        expected = (1 << 1) | (1 << 2)
        if engine0.errors or engine1.errors:
            expected |= 1 << 3
        assert status == expected, (
            f"cycle {cycle} (now {model_now}): uo_out={status:#04x} "
            f"expected {expected:#04x}"
        )

        model_now = (model_now + 1) & TIMEBASE_MASK


#: Comparison length for the TX round trip. Override for quick runs.
QUEUE_COMPARE_CYCLES = int(os.environ.get("V3_QUEUE_COMPARE_CYCLES", "2000"))

#: Engine 0 pulls one byte per loop and drives pin 0 from its least significant
#: bit; when the queue empties the PULL stalls and the pin stays low. The
#: waveform therefore depends on the exact bytes the host pushed.
TX_PROGRAM = """
start:
    PULL reg=0
    LI reg=1, value=1
    AND dst=0, src=1
    BREQ reg=0, value=0, target=low
    OUT pin=0, level=1, offset=4
    JMP target=wait
low:
    OUT pin=0, level=0, offset=4
wait:
    ADVANCE ticks=40
    WAITTIME
    OUT pin=0, level=0, offset=4
    ADVANCE ticks=40
    WAITTIME
    JMP target=start
"""

#: The bytes the host pushes; the low bits 1,0,1,0,1,0 drive pin 0.
TX_BYTES = bytes([0x01, 0x02, 0x03, 0x00, 0x05, 0x08])

#: Engine 0 pushes two receive records with a zeroed input shift register and
#: then halts, so the host can pop them back.
RX_PROGRAM = """
    RX meta=0x5A
    RX meta=0x3C
    HALT
"""


def _build_tx_requests(image0, data):
    """Write engine 0's image, configure it, push the bytes, then start it."""
    write0 = COMMANDS["WRITE"]
    requests = [Frame(write0, addr, word) for addr, word in enumerate(image0.words)]
    requests.append(Frame(COMMANDS["CONFIG"], 0, OWNER0))
    for start in range(0, len(data), 4):
        chunk = data[start : start + 4]
        payload = 0
        for index, byte in enumerate(chunk):
            payload |= byte << (8 * (3 - index))
        requests.append(Frame(COMMANDS["TXPUSH"], len(chunk), payload))
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    return requests


async def _run_setup(chip, requests):
    """Clock every setup request, checking each reply; START skips its idle."""
    await chip.frame(pack(*requests[0]))
    for index in range(1, len(requests)):
        is_start = index == len(requests) - 1
        miso = await chip.frame(pack(*requests[index]), finish=not is_start)
        _check_reply(index - 1, requests[index - 1], miso)


async def _await_engine0(dut):
    """Return the timebase value at the first cycle engine 0 reports running."""
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if (int(dut.uo_out.value) >> 1) & 1:
            return int(dut.u_timebase.now.value)
    raise AssertionError("engine 0 never started after START")


@cocotb.test()
async def test_v3_txpush_feeds_pull_matching_the_model(dut):
    """Bytes pushed with TXPUSH drain through PULL into the exact model waveform."""
    image0 = assemble(TX_PROGRAM)
    chip = Chip(dut)
    await chip.reset()

    await _run_setup(chip, _build_tx_requests(image0, TX_BYTES))
    assert int(dut.uio_oe.value) == 0, "a pin was driven before START"
    now_post = await _await_engine0(dut)

    start_now = (now_post - 1) & TIMEBASE_MASK
    model_now = now_post

    pins = [model.PinCell(open_drain=False) for _ in range(PIN_COUNT)]
    engine0 = model.Engine(pins=pins, owner=OWNER0)
    engine0.load(image0)
    # The host pushed these before START, so the model queue starts filled too.
    engine0.queues.push_tx(TX_BYTES)
    engine0.start(start_now)

    def model_uio():
        out = oe = 0
        for i, cell in enumerate(pins):
            value, enable = cell.drive()
            out |= (value & 1) << i
            oe |= (enable & 1) << i
        return out, oe

    for cycle in range(QUEUE_COMPARE_CYCLES):
        await FallingEdge(dut.clk)
        chip._drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        for cell in pins:
            cell.observe(0, model_now)
        await ReadOnly()

        await RisingEdge(dut.clk)
        engine0.step(model_now)
        for cell in pins:
            cell.tick(model_now)

        await ReadOnly()
        dut_now = int(dut.u_timebase.now.value)
        assert dut_now == (model_now + 1) & TIMEBASE_MASK, (
            f"cycle {cycle}: timebase diverged"
        )
        dut_out = int(dut.uio_out.value) & 0xFF
        dut_oe = int(dut.uio_oe.value) & 0xFF
        m_out, m_oe = model_uio()
        assert (dut_out, dut_oe) == (m_out, m_oe), (
            f"cycle {cycle} (now {model_now}): uio diverged, "
            f"dut (out={dut_out:#04x}, oe={dut_oe:#04x}) "
            f"model (out={m_out:#04x}, oe={m_oe:#04x})"
        )
        model_now = (model_now + 1) & TIMEBASE_MASK


async def _transact(chip, requests):
    """Send requests plus a trailing NOP, returning one reply per request."""
    nop = Frame(COMMANDS["NOP"], 0, 0)
    received = [unpack(await chip.frame(pack(*request))) for request in (*requests, nop)]
    return received[1:]


@cocotb.test()
async def test_v3_rxpop_returns_records_then_rejects_when_empty(dut):
    """RX records read back through RXPOP in order, then an empty queue rejects."""
    image0 = assemble(RX_PROGRAM)
    chip = Chip(dut)
    await chip.reset()

    # Work out the records the model produces for the same program.
    pins = [model.PinCell(open_drain=False) for _ in range(PIN_COUNT)]
    engine0 = model.Engine(pins=pins, owner=OWNER0)
    engine0.load(image0)
    engine0.start(0)
    now = 0
    while not engine0.halted and now < 16:
        engine0.step(now)
        for cell in pins:
            cell.tick(now)
        now += 1
    expected = [engine0.queues.pop_rx(), engine0.queues.pop_rx()]
    assert expected == [(0, 0x5A), (0, 0x3C)], f"model produced {expected}"

    setup = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    setup.append(Frame(COMMANDS["START"], 0, 0b01))
    replies = await _transact(chip, setup)
    for index, (request, reply) in enumerate(zip(setup, replies)):
        assert not reply.cmd & REJECT_BIT, f"setup frame {index} {request} rejected"

    # The engine has long since run and halted; pop the two records, then one
    # more to see the empty-queue rejection.
    pop = Frame(COMMANDS["RXPOP"], 0, 0)
    replies = await _transact(chip, [pop, pop, pop])
    for reply, (value, meta) in zip(replies, expected):
        assert not reply.cmd & REJECT_BIT, "a non-empty RXPOP was rejected"
        assert reply.payload == (value << 8) | meta, (
            f"RXPOP payload {reply.payload:#06x} != expected {(value << 8) | meta:#06x}"
        )
    assert replies[2].cmd & REJECT_BIT, "an empty RXPOP was not rejected"
    assert replies[2].payload == 0, "a rejected RXPOP must carry a zero payload"
