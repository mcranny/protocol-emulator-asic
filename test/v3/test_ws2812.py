"""Full-chip WS2812 firmware on ``v3_project``.

The transmit program is loaded through the host SPI port, fed a count byte and
three LEDs' worth of colour bytes with TXPUSH, started, and its ``uio`` pin is
decoded with the independent
:class:`~protocol_emulator.v3.peers.Ws2812Receiver`. It uses the same generator
as the model tests, so the silicon and the model are checked against one oracle.
"""
import cocotb
from cocotb.triggers import FallingEdge, ReadOnly

from protocol_emulator.v3 import CLOCK_HZ
from protocol_emulator.v3.firmware import ws2812_tx
from protocol_emulator.v3.host import COMMANDS, Frame
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import Ws2812Receiver
from support.v3_chip import (
    Chip,
    await_running,
    command,
    run_setup,
    txpush_requests,
    write_image_requests,
)

PIN = 0
#: Clocks per WS2812 symbol period (1.25 us at 48 MHz).
PERIOD = round(1.25e-6 * CLOCK_HZ)
#: Reset-low latch in clocks (300 us at 48 MHz).
RESET = round(300e-6 * CLOCK_HZ)


@cocotb.test()
async def test_v3_ws2812_tx_drives_three_leds(dut):
    """TXPUSH a count byte and nine colour bytes; decode the strip off uio."""
    data = bytes.fromhex("00ff00" "ff0000" "0000ff")
    frame = bytes([len(data)]) + data
    image = assemble(ws2812_tx(PIN))
    chip = Chip(dut)
    await chip.reset()

    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, 1 << PIN))
    requests += txpush_requests(frame, engine=0)
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    await run_setup(chip, requests)
    await await_running(dut, engine=0)

    waveform = []
    last_level = None
    record_cycles = len(data) * 8 * PERIOD + RESET + 400
    for _ in range(record_cycles):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = 0
        await ReadOnly()
        oe = (int(dut.uio_oe.value) >> PIN) & 1
        out = (int(dut.uio_out.value) >> PIN) & 1
        level = out if oe else 0
        now = int(dut.u_timebase.now.value)
        if level != last_level:
            waveform.append((now, level))
            last_level = level

    end = int(dut.u_timebase.now.value)
    result = Ws2812Receiver().decode(waveform, end=end)
    assert result.data == data, f"decoded {result.data.hex()}, expected {data.hex()}"
    assert result.violations == [], f"unexpected violations {result.violations}"
    last_fall = max(tick for tick, level in waveform if level == 0)
    assert end - last_fall >= RESET, f"reset low {end - last_fall} ticks is under {RESET}"
