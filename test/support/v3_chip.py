"""Host SPI driver for full-chip firmware cocotb tests.

Wraps the 48-clock host frame protocol so a firmware test can load an engine's
program, configure ownership, push transmit bytes, start, and read receive
records without re-implementing the SPI bit timing. The frame timing matches the
full-chip differential harness, so a program loaded through here runs under the
same transport the differential test proves.
"""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from protocol_emulator.v3.host import (
    COMMANDS,
    ENGINE_BIT,
    FRAME_BITS,
    REJECT_BIT,
    Frame,
    pack,
    unpack,
)

#: Clock period in nanoseconds (48 MHz), positional unit string for cocotb 1.9.
CLOCK_NS = 20.834
#: System clocks per SCK half-period.
HALF = 4
#: System clocks between CS_n falling and the first SCK edge, and after the last.
GUARD = 4
#: System clocks CS_n is held high between frames.
IDLE = 6


def _bits(value):
    return [(value >> (FRAME_BITS - 1 - i)) & 1 for i in range(FRAME_BITS)]


def _word(samples):
    value = 0
    for bit in samples:
        value = (value << 1) | (bit & 1)
    return value


def command(name, engine=0):
    """Return the command byte for ``name`` addressed to ``engine``."""
    return COMMANDS[name] | (ENGINE_BIT if engine else 0)


class Chip:
    """Drives the SPI port of the full chip and reads its status pins."""

    def __init__(self, dut):
        self.dut = dut

    def drive_spi(self, sck, cs_n, mosi):
        """Present the three SPI inputs on ``ui_in``, holding the rest low."""
        self.dut.ui_in.value = (sck & 1) | ((cs_n & 1) << 1) | ((mosi & 1) << 2)

    async def ticks(self, count):
        for _ in range(count):
            await FallingEdge(self.dut.clk)

    async def reset(self):
        dut = self.dut
        self.drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        dut.ena.value = 1
        dut.rst_n.value = 0
        cocotb.start_soon(Clock(dut.clk, CLOCK_NS, "ns").start())
        for _ in range(4):
            await FallingEdge(dut.clk)
        dut.rst_n.value = 1
        await FallingEdge(dut.clk)

    async def frame(self, value, finish=True):
        """Clock one 48-bit frame, MSB first, returning the 48 MISO samples."""
        dut = self.dut
        bits = _bits(value)
        self.drive_spi(sck=0, cs_n=0, mosi=0)
        await self.ticks(GUARD)
        samples = []
        for bit in bits:
            self.drive_spi(sck=0, cs_n=0, mosi=bit)
            await self.ticks(2)
            samples.append(int(dut.uo_out.value) & 1)
            self.drive_spi(sck=1, cs_n=0, mosi=bit)
            await self.ticks(HALF)
            self.drive_spi(sck=0, cs_n=0, mosi=bit)
            await self.ticks(HALF - 2)
        await self.ticks(GUARD)
        self.drive_spi(sck=0, cs_n=1, mosi=0)
        if finish:
            await self.ticks(IDLE)
        return _word(samples)


def _check_reply(index, request, reply_word):
    reply = unpack(reply_word)
    if reply.cmd & REJECT_BIT:
        raise AssertionError(f"setup frame {index} {request} was rejected by the chip")
    if reply.cmd != request.cmd or reply.addr != request.addr:
        raise AssertionError(
            f"setup frame {index} {request} got a mismatched reply "
            f"(cmd 0x{reply.cmd:02X}, addr {reply.addr})"
        )


async def run_setup(chip, requests):
    """Clock every setup request, checking each reply; the last skips its idle.

    The chip answers one frame late, so reply ``i`` rides out on request
    ``i + 1``'s MISO and the first frame's MISO is discarded. The final request
    (expected to be ``START``) is clocked without its trailing idle so the
    caller can watch the engines start immediately.
    """
    await chip.frame(pack(*requests[0]))
    for index in range(1, len(requests)):
        is_last = index == len(requests) - 1
        miso = await chip.frame(pack(*requests[index]), finish=not is_last)
        _check_reply(index - 1, requests[index - 1], miso)


async def await_running(dut, engine=0):
    """Return the timebase value at the first cycle ``engine`` reports running."""
    status_bit = 1 + engine
    for _ in range(128):
        await RisingEdge(dut.clk)
        await ReadOnly()
        if (int(dut.uo_out.value) >> status_bit) & 1:
            return int(dut.u_timebase.now.value)
    raise AssertionError(f"engine {engine} never started after START")


async def transact(chip, requests):
    """Send requests plus a trailing NOP, returning one reply per request."""
    nop = Frame(COMMANDS["NOP"], 0, 0)
    received = [unpack(await chip.frame(pack(*request))) for request in (*requests, nop)]
    return received[1:]


def write_image_requests(image, engine=0):
    """Return the WRITE frames that load ``image`` into ``engine``."""
    write = command("WRITE", engine)
    return [Frame(write, addr, word) for addr, word in enumerate(image.words)]


def txpush_requests(data, engine=0):
    """Return the TXPUSH frames that queue ``data`` for ``engine``."""
    push = command("TXPUSH", engine)
    requests = []
    for start in range(0, len(data), 4):
        chunk = data[start : start + 4]
        payload = 0
        for index, byte in enumerate(chunk):
            payload |= byte << (8 * (3 - index))
        requests.append(Frame(push, len(chunk), payload))
    return requests
