"""Full-chip UART firmware on ``v3_project``.

The transmit program is loaded through the host SPI port, fed bytes with
TXPUSH, started, and its ``uio`` pin is decoded with the independent
:class:`~protocol_emulator.v3.peers.UartReceiver`. The receive program is driven
on ``uio_in`` with a :class:`~protocol_emulator.v3.peers.UartTransmitter`
waveform and its bytes are read back with RXPOP. Both use the same generators as
the model tests, so the silicon and the model are checked against one oracle.
"""
import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from protocol_emulator.v3 import timing
from protocol_emulator.v3.firmware import uart_rx, uart_tx
from protocol_emulator.v3.firmware.uart import RX_META_OK
from protocol_emulator.v3.host import COMMANDS, Frame
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import UartReceiver, UartTransmitter
from support.v3_chip import (
    Chip,
    await_running,
    command,
    run_setup,
    transact,
    txpush_requests,
    write_image_requests,
)

PIN = 0
BAUD = 115200
BIT = timing.UART[BAUD]


def _level_source(changes):
    """Level-at-tick lookup over ``(tick, level)`` change points."""
    ordered = list(changes)

    def at(tick):
        level = ordered[0][1]
        for change_tick, change_level in ordered:
            if change_tick > tick:
                break
            level = change_level
        return level

    return at


@cocotb.test()
async def test_v3_uart_tx_drives_the_pin(dut):
    """TXPUSH bytes drain through the transmit firmware and decode off uio."""
    data = b"U3"
    image = assemble(uart_tx(PIN, BAUD))
    chip = Chip(dut)
    await chip.reset()

    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, 1 << PIN))
    requests += txpush_requests(data, engine=0)
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    await run_setup(chip, requests)
    now_post = await await_running(dut, engine=0)

    waveform = []
    last_level = None
    record_cycles = (len(data) * 11 + 4) * BIT
    for _ in range(record_cycles):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        await ReadOnly()
        oe = (int(dut.uio_oe.value) >> PIN) & 1
        out = (int(dut.uio_out.value) >> PIN) & 1
        level = out if oe else 1
        now = int(dut.u_timebase.now.value)
        if level != last_level:
            waveform.append((now, level))
            last_level = level

    end = int(dut.u_timebase.now.value)
    result = UartReceiver(BIT).decode(waveform, end=end)
    assert result.data == data, f"decoded {result.data!r}, expected {data!r}"
    assert result.violations == [], f"unexpected violations {result.violations}"


@cocotb.test()
async def test_v3_uart_rx_captures_the_waveform(dut):
    """A transmitter waveform on uio_in is received and read back with RXPOP."""
    message = b"\x3c\xa5"
    image = assemble(uart_rx(PIN, BAUD))
    chip = Chip(dut)
    await chip.reset()

    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, 0))
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    await run_setup(chip, requests)
    now_post = await await_running(dut, engine=0)

    waveform = UartTransmitter(BIT).encode(message, start=20, gap_bits=1)
    source = _level_source(waveform)
    drive_cycles = waveform[-1][0] + 12 * BIT
    for _ in range(drive_cycles):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        now = int(dut.u_timebase.now.value)
        rel = (now - now_post) & 0xFFFFFF
        dut.uio_in.value = source(rel) << PIN
        await ReadOnly()

    # Leave the input idle high before popping the records over SPI.
    await FallingEdge(dut.clk)
    dut.uio_in.value = 1 << PIN
    replies = await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0) for _ in message])
    records = [((reply.payload >> 8) & 0xFF, reply.payload & 0xFF) for reply in replies]
    assert records == [(byte, RX_META_OK) for byte in message], f"got {records}"
