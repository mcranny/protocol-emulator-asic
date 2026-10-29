"""Full-chip I2C controller firmware on ``v3_project``.

The controller program is loaded over the host SPI port, configured with SDA and
SCL open-drain, fed an operation stream with TXPUSH, started, and run. A small
software target answers on the wires: it watches the chip's open-drain outputs
(a line is pulled low only while ``uio_oe`` names it), pulls ``uio_in`` low to
acknowledge or to present read data, and otherwise lets the pull-up hold the
line high. The resolved open-drain bus is recorded and decoded with the
independent :class:`~protocol_emulator.v3.peers.I2cTarget`, and the chip's own
receive records are read back with RXPOP.
"""
import cocotb
from cocotb.triggers import FallingEdge, NextTimeStep, ReadOnly

from protocol_emulator.v3 import timing
from protocol_emulator.v3.firmware import i2c_controller
from protocol_emulator.v3.firmware.i2c import (
    OP_READ_NACK,
    OP_START,
    OP_STOP,
    OP_WRITE,
    RX_META_ACK,
    RX_META_LAST,
    RX_META_READ,
)
from protocol_emulator.v3.host import COMMANDS, Frame
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import I2cBus, I2cTarget
from support.v3_chip import (
    Chip,
    await_running,
    command,
    run_setup,
    transact,
    txpush_requests,
    write_image_requests,
)

SDA = 1
SCL = 2
BOTH = (1 << SDA) | (1 << SCL)


class RtlTarget:
    """Software I2C target reacting to the chip's open-drain pins.

    Call :meth:`step` once per clock with the chip's ``uio_oe`` byte. It returns
    the ``(scl, sda)`` levels the chip must see on ``uio_in``: the wired-AND of
    the chip's drive and this target's own pulls. The target acknowledges a
    matched address and every written byte, and drives ``read_data`` on a read.
    """

    def __init__(self, address, *, ack_address=True, read_data=b""):
        self.address = address
        self.ack_address = ack_address
        self.read_data = read_data
        self.prev_scl = 1
        self.prev_sda = 1
        self.started = False
        self.bit = 0
        self.byte = 0
        self.direction = 0
        self.shift = 0
        self.drive_low = False
        self.read_bits = []

    def step(self, oe):
        chip_scl = 0 if (oe >> SCL) & 1 else 1
        # SDA seen by everyone is the chip's drive AND this target's pull.
        chip_sda = 0 if (oe >> SDA) & 1 else 1
        # Drive this tick from the state settled on the previous tick so the
        # target's SDA edge never lands on the same tick as an SCL edge.
        drive_low = self.drive_low
        if chip_scl == 1 and self.prev_scl == 1 and chip_sda == 0 and self.prev_sda == 1:
            self.started = True
            self.bit = 0
            self.byte = 0
            self.direction = 0
            self.shift = 0
            self.drive_low = False
        rising = self.prev_scl == 0 and chip_scl == 1
        falling = self.prev_scl == 1 and chip_scl == 0
        if self.started and rising:
            self.bit += 1
            if self.bit <= 8:
                self.shift = ((self.shift << 1) | chip_sda) & 0xFF
        if self.started and falling:
            if self.bit == 8:
                if self.byte == 0:
                    self.direction = self.shift & 1
                    self.drive_low = self.ack_address and (self.shift >> 1) == self.address
                elif self.direction == 0:
                    self.drive_low = True
                else:
                    self.drive_low = False
            elif self.bit == 9:
                self.bit = 0
                self.byte += 1
                self.shift = 0
                if self.direction == 1 and self.byte >= 1:
                    self._load_read_byte()
                    self.drive_low = self.read_bits.pop(0) == 0
                else:
                    self.drive_low = False
            elif self.direction == 1 and self.byte >= 1 and 1 <= self.bit <= 7:
                self.drive_low = self.read_bits.pop(0) == 0 if self.read_bits else False
        self.prev_scl = chip_scl
        self.prev_sda = chip_sda
        sda_level = 0 if (drive_low or chip_sda == 0) else 1
        return chip_scl, sda_level

    def _load_read_byte(self):
        index = self.byte - 1
        value = self.read_data[index] if index < len(self.read_data) else 0xFF
        self.read_bits = [(value >> (7 - i)) & 1 for i in range(8)]


async def _setup(chip, image):
    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, BOTH | (BOTH << 8)))
    await run_setup(chip, requests)


async def _run_transaction(dut, rate, ops, target, record_bits):
    """Load and start the controller, play ``ops``, and return the bus samples."""
    image = assemble(i2c_controller(SDA, SCL, rate))
    chip = Chip(dut)
    await chip.reset()

    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, BOTH | (BOTH << 8)))
    requests += txpush_requests(ops, engine=0)
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    await run_setup(chip, requests)
    await await_running(dut, engine=0)

    samples = []
    last = None
    for _ in range(record_bits):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        await ReadOnly()
        oe = int(dut.uio_oe.value)
        scl, sda = target.step(oe)
        now = int(dut.u_timebase.now.value)
        if (scl, sda) != last:
            samples.append((now, I2cBus(scl, sda)))
            last = (scl, sda)
        await NextTimeStep()
        dut.uio_in.value = (scl << SCL) | (sda << SDA)
    return chip, samples


@cocotb.test()
async def test_v3_i2c_write_100k(dut):
    """The chip writes one byte to a target that acknowledges."""
    rate = 100_000
    low, high = timing.I2C[rate]
    address = 0x50
    ops = bytes([OP_START, (address << 1) | 0, OP_WRITE, 0xAB, OP_STOP])
    target = RtlTarget(address)
    record = 40 * (low + high)
    chip, samples = await _run_transaction(dut, rate, ops, target, record)
    result = I2cTarget(rate).decode(samples)
    assert result.violations == [], f"violations {result.violations}"
    assert result.data == bytes([(address << 1) | 0, 0xAB]), f"data {result.data!r}"
    kinds = [e.kind for e in result.events]
    assert kinds == ["start", "byte", "ack", "byte", "ack", "stop"], f"events {kinds}"
    replies = await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0) for _ in range(2)])
    records = [((r.payload >> 8) & 0xFF, r.payload & 0xFF) for r in replies]
    assert records == [(0, RX_META_ACK), (0, RX_META_ACK)], f"records {records}"


@cocotb.test()
async def test_v3_i2c_read_100k(dut):
    """The chip reads one byte, NACKs it, and recovers the target's data."""
    rate = 100_000
    low, high = timing.I2C[rate]
    address = 0x50
    ops = bytes([OP_START, (address << 1) | 1, OP_READ_NACK, OP_STOP])
    target = RtlTarget(address, read_data=bytes([0x3C]))
    record = 40 * (low + high)
    chip, samples = await _run_transaction(dut, rate, ops, target, record)
    result = I2cTarget(rate).decode(samples)
    assert result.violations == [], f"violations {result.violations}"
    replies = await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0) for _ in range(2)])
    records = [((r.payload >> 8) & 0xFF, r.payload & 0xFF) for r in replies]
    assert records == [
        (0, RX_META_ACK),
        (0x3C, RX_META_READ | RX_META_LAST),
    ], f"records {records}"


@cocotb.test()
async def test_v3_i2c_write_400k(dut):
    """The same write transaction at 400 kHz on the chip."""
    rate = 400_000
    low, high = timing.I2C[rate]
    address = 0x50
    ops = bytes([OP_START, (address << 1) | 0, OP_WRITE, 0x5A, OP_STOP])
    target = RtlTarget(address)
    record = 40 * (low + high)
    chip, samples = await _run_transaction(dut, rate, ops, target, record)
    result = I2cTarget(rate).decode(samples)
    assert result.violations == [], f"violations {result.violations}"
    assert result.data == bytes([(address << 1) | 0, 0x5A]), f"data {result.data!r}"
    replies = await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0) for _ in range(2)])
    records = [((r.payload >> 8) & 0xFF, r.payload & 0xFF) for r in replies]
    assert records == [(0, RX_META_ACK), (0, RX_META_ACK)], f"records {records}"
