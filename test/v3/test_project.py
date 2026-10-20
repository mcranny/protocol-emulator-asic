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
        engine1.step(model_now, crc_grant=not engine0.crc_used)
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


@cocotb.test()
async def test_v3_host_read_returns_the_addressed_word(dut):
    """A host READ returns the word last written to its own address.

    WRITE A=x, WRITE B=y (x != y), then READ A, READ B, READ A. Each reply must
    carry the word written to the address in its own frame, not the previous
    memory output.
    """
    chip = Chip(dut)
    await chip.reset()

    addr_a, addr_b = 0x05, 0x11
    word_x, word_y = 0xAAAA_0001, 0xBBBB_0002
    assert word_x != word_y

    requests = [
        Frame(COMMANDS["WRITE"], addr_a, word_x),
        Frame(COMMANDS["WRITE"], addr_b, word_y),
        Frame(COMMANDS["READ"], addr_a, 0),
        Frame(COMMANDS["READ"], addr_b, 0),
        Frame(COMMANDS["READ"], addr_a, 0),
    ]
    replies = await _transact(chip, requests)
    for index, request in enumerate(requests):
        assert not replies[index].cmd & REJECT_BIT, f"frame {index} {request} rejected"
        assert replies[index].addr == request.addr, "reply addr must echo the request"
    assert replies[2].payload == word_x, (
        f"READ A got {replies[2].payload:#010x}, expected {word_x:#010x}"
    )
    assert replies[3].payload == word_y, (
        f"READ B got {replies[3].payload:#010x}, expected {word_y:#010x}"
    )
    assert replies[4].payload == word_x, (
        f"READ A (second) got {replies[4].payload:#010x}, expected {word_x:#010x}"
    )


@cocotb.test()
async def test_v3_partial_start_rejects_and_starts_neither_engine(dut):
    """A START is all-or-nothing: a rejected START strobes neither engine.

    Engine 0's image is complete and engine 1's is not. START 0b11 must reply
    rejected and leave both engines stopped. A following START 0b01 is accepted
    and runs engine 0 alone.
    """
    image0 = assemble("loop:\n    JMP target=loop\n")
    chip = Chip(dut)
    await chip.reset()

    # Load engine 0 only; engine 1's program memory stays incomplete.
    writes = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    write_replies = await _transact(chip, writes)
    for index, request in enumerate(writes):
        assert not write_replies[index].cmd & REJECT_BIT, f"write {index} {request} rejected"

    # START both: engine 1 is incomplete, so the whole frame is rejected.
    reply = (await _transact(chip, [Frame(COMMANDS["START"], 0, 0b11)]))[0]
    assert reply.cmd & REJECT_BIT, "a partial START was not rejected"

    # Neither engine may run or drive a pin after the rejected START.
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        status = int(dut.uo_out.value)
        assert not (status >> 1) & 1, "engine 0 started on a rejected START"
        assert not (status >> 2) & 1, "engine 1 started on a rejected START"
    assert int(dut.uio_oe.value) == 0, "a pin was driven after a rejected START"

    # START engine 0 alone; its image is complete, so it runs. Leave the
    # read-only phase before the frame driver writes the SPI pins.
    await RisingEdge(dut.clk)
    await chip.frame(pack(COMMANDS["START"], 0, 0b01), finish=False)
    started = False
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        status = int(dut.uo_out.value)
        if (status >> 1) & 1:
            assert not (status >> 2) & 1, "engine 1 ran from a 0b01 START"
            started = True
            break
    assert started, "engine 0 did not start from an accepted START"


#: Open-drain exercise: SET then OE on a pin left released high. OE must keep
#: the latched high value (open drain then releases) rather than reading the
#: gated pad level (always low on an open-drain pin), which would wrongly pull
#: the pin low. SET then keeps the latched enable while changing the value.
OPEN_DRAIN_PROGRAM = """
loop:
    SET mask=0x01, value=0x01, offset=2
    ADVANCE ticks=20
    WAITTIME
    OE mask=0x01, oe=0x01, offset=2
    ADVANCE ticks=20
    WAITTIME
    SET mask=0x01, value=0x00, offset=2
    ADVANCE ticks=20
    WAITTIME
    OE mask=0x01, oe=0x00, offset=2
    ADVANCE ticks=20
    WAITTIME
    JMP target=loop
"""

#: Comparison length for the open-drain directed run. A few loop iterations is
#: enough to exercise every SET/OE ordering on the open-drain pin.
OPEN_DRAIN_CYCLES = int(os.environ.get("V3_OPEN_DRAIN_CYCLES", "400"))


@cocotb.test()
async def test_v3_open_drain_preserves_latched_state_across_set_and_oe(dut):
    """SET and OE preserve the untouched half of the latched state on an
    open-drain pin.

    Engine 0 owns an open-drain pin 0. A commit must carry the engine's latched
    value/oe for the half it does not change, not the open-drain-gated pad level
    (which never drives high). The run is compared against the model, which
    preserves the latched state, so any gated-level leak diverges.
    """
    image0 = assemble(OPEN_DRAIN_PROGRAM)
    chip = Chip(dut)
    await chip.reset()

    # Write engine 0's image, configure it as owner 0x0F with pin 0 open-drain,
    # then start it alone.
    requests = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    requests.append(Frame(COMMANDS["CONFIG"], 0, (0x01 << 8) | OWNER0))
    requests.append(Frame(COMMANDS["START"], 0, 0b01))

    await chip.frame(pack(*requests[0]))
    for index in range(1, len(requests)):
        is_start = index == len(requests) - 1
        miso = await chip.frame(pack(*requests[index]), finish=not is_start)
        _check_reply(index - 1, requests[index - 1], miso)

    # Catch the cycle engine 0 latches running so the model aligns to the same
    # timebase value.
    now_post = None
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        status = int(dut.uo_out.value)
        if (status >> 1) & 1:
            assert not (status >> 2) & 1, "engine 1 started from a 0b01 START"
            now_post = int(dut.u_timebase.now.value)
            break
    assert now_post is not None, "engine 0 never started"

    start_now = (now_post - 1) & TIMEBASE_MASK
    model_now = now_post

    pins = [model.PinCell(open_drain=bool(i == 0)) for i in range(PIN_COUNT)]
    engine0 = model.Engine(pins=pins, owner=OWNER0, open_drain=0x01)
    engine0.load(image0)
    engine0.start(start_now)

    def model_uio():
        out = oe = 0
        for i, cell in enumerate(pins):
            value, enable = cell.drive()
            out |= (value & 1) << i
            oe |= (enable & 1) << i
        return out, oe

    for cycle in range(OPEN_DRAIN_CYCLES):
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
        model_now = (model_now + 1) & TIMEBASE_MASK


async def _running(dut) -> bool:
    """True once engine 0 reports running within 64 clocks, else False."""
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if (int(dut.uo_out.value) >> 1) & 1:
            return True
    return False


@cocotb.test()
async def test_v3_clear_invalidates_so_a_partial_reload_cannot_start(dut):
    """CLEAR drops the validity bitmap, so a partial reload cannot start.

    A full image is written, then CLEAR invalidates it and only one word is
    rewritten. START must be refused and start nothing. Rewriting the whole
    image makes START succeed again, so CLEAR is recoverable.
    """
    image0 = assemble("loop:\n    JMP target=loop\n")
    chip = Chip(dut)
    await chip.reset()

    writes = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    write_replies = await _transact(chip, writes)
    for index, request in enumerate(writes):
        assert not write_replies[index].cmd & REJECT_BIT, f"write {index} {request} rejected"

    # CLEAR while stopped is accepted; a single rewrite leaves the image short.
    clear_reply = (await _transact(chip, [Frame(COMMANDS["CLEAR"], 0, 0)]))[0]
    assert not clear_reply.cmd & REJECT_BIT, "CLEAR was rejected while stopped"
    await _transact(chip, [Frame(COMMANDS["WRITE"], 0, image0.words[0])])

    # START must be refused because the image is incomplete after the CLEAR.
    reply = (await _transact(chip, [Frame(COMMANDS["START"], 0, 0b01)]))[0]
    assert reply.cmd & REJECT_BIT, "START ran a partial reload after CLEAR"
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        assert not (int(dut.uo_out.value) >> 1) & 1, "engine 0 started from an incomplete image"

    # Rewrite the whole image: START is accepted again.
    await RisingEdge(dut.clk)
    await _transact(chip, writes)
    await RisingEdge(dut.clk)
    await chip.frame(pack(COMMANDS["START"], 0, 0b01), finish=False)
    assert await _running(dut), "engine 0 did not start after a full reload"


@cocotb.test()
async def test_v3_clear_is_rejected_while_the_engine_runs(dut):
    """CLEAR is refused while the target engine runs and does not invalidate."""
    image0 = assemble("loop:\n    JMP target=loop\n")
    chip = Chip(dut)
    await chip.reset()

    writes = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    await _transact(chip, writes)

    await chip.frame(pack(COMMANDS["START"], 0, 0b01), finish=False)
    assert await _running(dut), "engine 0 never started"

    # CLEAR while running must be rejected and leave the engine running.
    await RisingEdge(dut.clk)
    reply = (await _transact(chip, [Frame(COMMANDS["CLEAR"], 0, 0)]))[0]
    assert reply.cmd & REJECT_BIT, "CLEAR was accepted while the engine ran"
    await RisingEdge(dut.clk)
    await ReadOnly()
    assert (int(dut.uo_out.value) >> 1) & 1, "engine 0 stopped after a rejected CLEAR"


def _crc_collision_program(pin: int) -> str:
    """A CRC loop that drives ``pin`` from the running CRC's low bit.

    Both engines run this in lockstep, so they request the shared CRC in the
    same cycles and collide. The pin waveform depends on the exact sequence of
    CRC shifts, so a dropped (rather than stalled) collision changes the output.
    """
    return f"""
    CRCCFG poly=0x1021, msb_first=1
    LI reg=0, value=0xFFFF
    CRCLOAD reg=0
loop:
    LI reg=1, value=1
    CRCBIT reg=1
    CRCGET reg=2
    LI reg=3, value=1
    AND dst=2, src=3
    BREQ reg=2, value=0, target=low
    OUT pin={pin}, level=1, offset=2
    JMP target=wait
low:
    OUT pin={pin}, level=0, offset=2
wait:
    ADVANCE ticks=6
    WAITTIME
    JMP target=loop
"""


#: Comparison length for the CRC-collision differential.
CRC_COLLISION_CYCLES = int(os.environ.get("V3_CRC_COLLISION_CYCLES", "600"))


@cocotb.test()
async def test_v3_shared_crc_collision_stalls_the_losing_engine(dut):
    """Two engines colliding on the shared CRC serialize, matching the model.

    Both engines run the same CRC loop, so they request the CRC in the same
    cycles. Engine 0 wins; engine 1 stalls and retries. The model applies the
    same grant, so the compared pin waveforms only agree if the RTL stalls the
    loser instead of dropping its request.
    """
    image0 = assemble(_crc_collision_program(0))
    image1 = assemble(_crc_collision_program(4))

    chip = Chip(dut)
    await chip.reset()

    requests = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    requests += [
        Frame(COMMANDS["WRITE"] | ENGINE_BIT, addr, word) for addr, word in enumerate(image1.words)
    ]
    requests.append(Frame(COMMANDS["CONFIG"], 0, OWNER0))
    requests.append(Frame(COMMANDS["CONFIG"] | ENGINE_BIT, 0, OWNER1))
    requests.append(Frame(COMMANDS["START"], 0, 0b11))

    await chip.frame(pack(*requests[0]))
    for index in range(1, len(requests)):
        is_start = index == len(requests) - 1
        miso = await chip.frame(pack(*requests[index]), finish=not is_start)
        _check_reply(index - 1, requests[index - 1], miso)

    now_post = None
    for _ in range(64):
        await RisingEdge(dut.clk)
        await ReadOnly()
        status = int(dut.uo_out.value)
        if (status >> 1) & 1:
            assert (status >> 2) & 1, "the two engines did not start on one cycle"
            now_post = int(dut.u_timebase.now.value)
            break
    assert now_post is not None, "the engines never started"

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

    saw_stall = False
    for cycle in range(CRC_COLLISION_CYCLES):
        await FallingEdge(dut.clk)
        chip._drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        for cell in pins:
            cell.observe(0, model_now)
        await ReadOnly()

        await RisingEdge(dut.clk)
        engine0.step(model_now)
        engine1.step(model_now, crc_grant=not engine0.crc_used)
        saw_stall = saw_stall or (engine1.stalled and engine0.crc_used)
        for cell in pins:
            cell.tick(model_now)

        await ReadOnly()
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
        model_now = (model_now + 1) & TIMEBASE_MASK

    assert saw_stall, "the two engines never collided on the CRC; test is not exercising the stall"


#: An engine 0 program that never halts, for the commands refused while it runs.
LOOP_PROGRAM = "loop:\n    JMP target=loop\n"


async def _load_and_start(chip, dut, image, engines=0b01):
    """Write ``image`` to every commanded engine, then START them."""
    requests = []
    for engine in (0, 1):
        if engines >> engine & 1:
            write = COMMANDS["WRITE"] | (ENGINE_BIT if engine else 0)
            requests += [Frame(write, addr, word) for addr, word in enumerate(image.words)]
    requests.append(Frame(COMMANDS["START"], 0, engines))
    replies = await _transact(chip, requests)
    for index, (request, reply) in enumerate(zip(requests, replies)):
        assert not reply.cmd & REJECT_BIT, f"setup frame {index} {request} rejected"


@cocotb.test()
async def test_v3_filter_defaults_to_every_pin_and_reads_back(dut):
    """The filter mask resets to 0xFF; a read returns it and changes nothing."""
    chip = Chip(dut)
    await chip.reset()
    assert int(dut.filter_mask_r.value) == 0xFF

    replies = await _transact(chip, [Frame(COMMANDS["FILTER"], 1, 0x00)])
    assert not replies[0].cmd & REJECT_BIT, "a FILTER read was rejected"
    assert replies[0].payload == 0xFF
    assert int(dut.filter_mask_r.value) == 0xFF, "a FILTER read wrote the mask"

    # An accepted write replies with the new mask and a read sees it.
    write, read = Frame(COMMANDS["FILTER"], 0, 0x3C), Frame(COMMANDS["FILTER"], 1, 0)
    replies = await _transact(chip, [write, read])
    assert not replies[0].cmd & REJECT_BIT, "a FILTER write was rejected while stopped"
    assert [r.payload for r in replies] == [0x3C, 0x3C]


@cocotb.test()
async def test_v3_filter_write_is_rejected_while_an_engine_runs(dut):
    """A FILTER write while an engine runs is rejected and leaves the mask."""
    chip = Chip(dut)
    await chip.reset()
    await _load_and_start(chip, dut, assemble(LOOP_PROGRAM))
    assert (int(dut.uo_out.value) >> 1) & 1, "engine 0 is not running"

    # The engine bit is ignored: neither address form may write while running.
    write0 = Frame(COMMANDS["FILTER"], 0, 0x00)
    write1 = Frame(COMMANDS["FILTER"] | ENGINE_BIT, 0, 0x00)
    read = Frame(COMMANDS["FILTER"], 1, 0)
    replies = await _transact(chip, [write0, write1, read])
    assert replies[0].cmd & REJECT_BIT, "a FILTER write was accepted while engine 0 ran"
    assert replies[1].cmd & REJECT_BIT, "a FILTER write was accepted while engine 0 ran"
    assert replies[0].payload == replies[1].payload == 0xFF, "a rejected write must reply the kept mask"
    assert not replies[2].cmd & REJECT_BIT, "a FILTER read was rejected while running"
    assert replies[2].payload == 0xFF
    assert int(dut.filter_mask_r.value) == 0xFF


@cocotb.test()
async def test_v3_an_unfiltered_pin_sees_a_glitch_the_filter_rejects(dut):
    """With pin 0 unfiltered and pin 1 filtered, a two-clock pulse on both
    reaches pin 0's synchronized level and never reaches pin 1's."""
    chip = Chip(dut)
    await chip.reset()
    replies = await _transact(chip, [Frame(COMMANDS["FILTER"], 0, 0xFE)])
    assert not replies[0].cmd & REJECT_BIT
    assert int(dut.filter_mask_r.value) == 0xFE

    seen = 0
    await FallingEdge(dut.clk)
    dut.uio_in.value = 0b11
    for cycle in range(12):
        await RisingEdge(dut.clk)
        await ReadOnly()
        seen |= int(dut.pin_level_w.value) & 0b11
        await FallingEdge(dut.clk)
        if cycle == 1:
            dut.uio_in.value = 0
    assert seen & 0b01, "the unfiltered pin never saw the pulse"
    assert not seen & 0b10, "the filtered pin passed a two-clock pulse"


@cocotb.test()
async def test_v3_caparm_arms_stops_and_records_read_back(dut):
    """CAPARM arms and stops capture; CAPREAD returns each record's sample and
    timestamp halves, a past-the-end index reads zero, and reading changes
    nothing."""
    chip = Chip(dut)
    await chip.reset()

    arm = Frame(COMMANDS["CAPARM"] | ENGINE_BIT, 0x00, 0x00A5_0000 | 0x81 << 8 | 0b00)
    replies = await _transact(chip, [arm])
    assert not replies[0].cmd & REJECT_BIT, "CAPARM arm was rejected"
    assert replies[0].payload == 0x81 << 8, "arm reply must echo {mask, mode} only"
    assert int(dut.u_capture.armed.value) == 1
    assert int(dut.u_capture.triggered.value) == 1, "mode 0 triggers on arm"

    # Raise pin 2 so capture records an input change.
    await FallingEdge(dut.clk)
    dut.uio_in.value = 0x04
    await chip._ticks(8)

    stop = Frame(COMMANDS["CAPARM"], 0x01, 0xFFFF_FFFF)
    status = Frame(COMMANDS["STATUS"], 1, 0)
    stamp = Frame(COMMANDS["STATUS"], 2, 0)
    replies = await _transact(chip, [stop, status, stamp])
    assert not replies[0].cmd & REJECT_BIT, "CAPARM stop was rejected"
    assert replies[0].payload == 0, "a stop must reply zero"
    word = replies[1].payload
    count = word >> 6 & 0x3F
    assert word & 0b111 == 0b010, f"expected stopped, triggered, not truncated: {word:#x}"
    assert word >> 3 & 0x7 == 3, "a stop after a trigger reports reason 3"
    assert count >= 2, f"expected the arm record and the pin change, count {count}"
    trigger_stamp = replies[2].payload

    reads = [Frame(COMMANDS["CAPREAD"], index << 1 | half, 0)
             for index in range(count + 1) for half in (0, 1)]
    replies = await _transact(chip, [*reads, status])
    assert not any(r.cmd & REJECT_BIT for r in replies), "a CAPREAD was rejected"
    records = [(replies[i].payload, replies[i + 1].payload) for i in range(0, 2 * count, 2)]
    for index, (sample, timestamp) in enumerate(records):
        assert int(dut.u_capture.records[index].value) == timestamp << 32 | sample, (
            f"record {index} read back {timestamp:#x}:{sample:#x}"
        )
    assert records[0][1] == trigger_stamp, "the arm record carries the trigger timestamp"
    assert records[0][0] & 0x08, "the arm record carries the trigger annotation"
    assert records[0][0] >> 24 == 0 and records[-1][0] >> 24 == 0x04
    stamps = [timestamp for _, timestamp in records]
    assert stamps == sorted(set(stamps)), "record timestamps must increase"
    assert replies[2 * count].payload == 0 and replies[2 * count + 1].payload == 0, (
        "a past-the-end CAPREAD must read zero"
    )
    assert replies[-1].payload == word, "reading records changed the capture state"


@cocotb.test()
async def test_v3_start_does_not_arm_capture(dut):
    """START runs an engine without arming capture."""
    chip = Chip(dut)
    await chip.reset()
    await _load_and_start(chip, dut, assemble(LOOP_PROGRAM))
    assert (int(dut.uo_out.value) >> 1) & 1, "engine 0 is not running"
    replies = await _transact(chip, [Frame(COMMANDS["STATUS"], 1, 0)])
    assert replies[0].payload == 0, f"START touched capture: {replies[0].payload:#x}"
    assert int(dut.u_capture.armed.value) == 0


@cocotb.test()
async def test_v3_status_reports_engines_and_capture_and_rejects_others(dut):
    """STATUS address 0 reports the selected engine, 1 capture state, 2 the
    trigger timestamp; any other address is rejected with a zero payload."""
    chip = Chip(dut)
    await chip.reset()

    # Engine 0 pushes two receive records and halts; engine 1 loops with three
    # bytes left in its transmit queue.
    image0, image1 = assemble(RX_PROGRAM), assemble(LOOP_PROGRAM)
    requests = [Frame(COMMANDS["WRITE"], addr, word) for addr, word in enumerate(image0.words)]
    requests += [Frame(COMMANDS["WRITE"] | ENGINE_BIT, addr, word)
                 for addr, word in enumerate(image1.words)]
    requests.append(Frame(COMMANDS["TXPUSH"] | ENGINE_BIT, 3, 0x0102_0300))
    requests.append(Frame(COMMANDS["START"], 0, 0b11))
    requests.append(Frame(COMMANDS["CAPARM"], 0, 0))
    replies = await _transact(chip, requests)
    assert not any(r.cmd & REJECT_BIT for r in replies), "setup was rejected"

    queries = [
        Frame(COMMANDS["STATUS"], 0, 0),
        Frame(COMMANDS["STATUS"] | ENGINE_BIT, 0, 0),
        Frame(COMMANDS["STATUS"] | ENGINE_BIT, 1, 0),
        Frame(COMMANDS["STATUS"], 2, 0),
        Frame(COMMANDS["STATUS"], 3, 0),
        Frame(COMMANDS["STATUS"] | ENGINE_BIT, 0x80, 0),
    ]
    replies = await _transact(chip, queries)
    assert not any(r.cmd & REJECT_BIT for r in replies[:4]), "a known STATUS address was rejected"

    def fields(word):
        return {
            "running": word & 1, "halted": word >> 1 & 1, "stalled": word >> 2 & 1,
            "fault": word >> 3 & 0xFF, "tx": word >> 11 & 0x1F, "rx": word >> 16 & 0x1F,
            "missed": word >> 21 & 0xFF, "top": word >> 29,
        }

    missed = int(dut.pin_missed_w.value)
    assert fields(replies[0].payload) == {
        "running": 0, "halted": 1, "stalled": 0, "fault": int(dut.e0_fault.value),
        "tx": 0, "rx": 2, "missed": missed, "top": 0,
    }
    assert fields(replies[1].payload) == {
        "running": 1, "halted": 0, "stalled": 0, "fault": int(dut.e1_fault.value),
        "tx": 3, "rx": 0, "missed": missed, "top": 0,
    }

    # A running engine's flag is recorded every clock, so the store has filled.
    capture = replies[2].payload
    assert capture & 0b111 == 0b110, f"expected triggered and truncated: {capture:#x}"
    assert capture >> 3 & 0x7 == 5, "a full store reports reason 5"
    assert capture >> 6 & 0x3F == 32
    assert capture >> 12 == 0
    assert replies[3].payload == int(dut.u_capture.trigger_timestamp.value) != 0

    for reply in replies[4:]:
        assert reply.cmd & REJECT_BIT, f"STATUS address {reply.addr} was not rejected"
        assert reply.payload == 0, "a rejected STATUS must carry a zero payload"
