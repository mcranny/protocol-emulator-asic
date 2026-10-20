"""Full-chip SPI firmware on ``v3_project``.

The controller program is loaded through the host SPI port, fed a byte with
TXPUSH, started, and its ``uio`` pins are decoded with the independent
:class:`~protocol_emulator.v3.peers.SpiTarget`, while the harness acts as a
mode-0 target on ``uio_in`` so the controller's received byte can be read back
with RXPOP. A second test loads a controller on engine 0 and a target on engine
1 and exchanges a byte each way entirely on chip, read back with RXPOP. Both use
the same generators as the model tests, so the silicon and the model are checked
against one oracle.
"""
import cocotb
from cocotb.triggers import FallingEdge, ReadOnly

from protocol_emulator.v3.firmware import spi_controller, spi_target
from protocol_emulator.v3.firmware.spi import RX_META
from protocol_emulator.v3.host import COMMANDS, Frame
from protocol_emulator.v3.isa import assemble
from protocol_emulator.v3.peers import SpiBus, SpiTarget
from support.v3_chip import (
    Chip,
    await_running,
    command,
    run_setup,
    transact,
    txpush_requests,
    write_image_requests,
)

SCLK, CS, MOSI, MISO = 0, 1, 2, 3
MODE = 0
#: Clocks a single-byte transfer takes end to end on chip, with headroom.
BYTE_CLOCKS = 48 * 16


def _wire(dut, pin, idle):
    """Resolve the wire level of protocol ``pin``: chip output when driven, else
    the value presented on ``uio_in`` (or ``idle`` for a pin the harness holds)."""
    oe = (int(dut.uio_oe.value) >> pin) & 1
    if oe:
        return (int(dut.uio_out.value) >> pin) & 1
    return idle


@cocotb.test()
async def test_v3_spi_controller_mode0(dut):
    """The controller drives uio; a reactive mode-0 target supplies MISO. The
    sent byte decodes off uio and the received byte reads back with RXPOP."""
    sent = 0x5A
    reply = 0xA5
    image = assemble(spi_controller(SCLK, CS, MOSI, MISO, MODE))
    chip = Chip(dut)
    await chip.reset()

    owner = (1 << SCLK) | (1 << CS) | (1 << MOSI)
    requests = write_image_requests(image, engine=0)
    requests.append(Frame(command("CONFIG"), 0, owner))
    requests += txpush_requests(bytes([sent]), engine=0)
    requests.append(Frame(COMMANDS["START"], 0, 0b01))
    await run_setup(chip, requests)
    await await_running(dut, engine=0)

    bits = [(reply >> (7 - i)) & 1 for i in range(8)]
    index = 0
    prev_cs = 1
    prev_sclk = 0
    miso = 0
    waveform = []
    last = None
    for _ in range(BYTE_CLOCKS):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = miso << MISO
        await ReadOnly()
        sclk = _wire(dut, SCLK, 0)
        cs = _wire(dut, CS, 1)
        mosi = _wire(dut, MOSI, 0)
        now = int(dut.u_timebase.now.value)
        state = (sclk, cs, mosi, miso)
        if state != last:
            waveform.append((now, SpiBus(sclk, cs, mosi, miso)))
            last = state
        # Reactive mode-0 target: present the first bit at chip-select assert,
        # then shift on every trailing (falling) edge.
        if prev_cs == 1 and cs == 0:
            miso = bits[0]
            index = 1
        elif cs == 0 and prev_sclk == 1 and sclk == 0:
            miso = bits[index] if index < 8 else 0
            index += 1
        elif cs == 1:
            miso = 0
            index = 0
        prev_cs, prev_sclk = cs, sclk

    await FallingEdge(dut.clk)
    result = SpiTarget(MODE).decode(waveform)
    assert result.data == bytes([sent]), f"decoded MOSI {result.data!r}, expected {sent:#04x}"
    assert result.violations == [], f"unexpected violations {result.violations}"

    reply_frames = await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0)])
    record = reply_frames[0]
    value, meta = (record.payload >> 8) & 0xFF, record.payload & 0xFF
    assert value == reply, f"controller received {value:#04x}, expected {reply:#04x}"
    assert meta == RX_META


@cocotb.test()
async def test_v3_spi_duplex_mode0(dut):
    """A controller on engine 0 and a target on engine 1 exchange one byte each
    way on chip; both received bytes read back with RXPOP."""
    to_target = 0x2D
    to_controller = 0xD4
    controller = assemble(spi_controller(SCLK, CS, MOSI, MISO, MODE))
    target = assemble(spi_target(SCLK, CS, MOSI, MISO, MODE))
    chip = Chip(dut)
    await chip.reset()

    requests = write_image_requests(controller, engine=0)
    requests += write_image_requests(target, engine=1)
    requests.append(Frame(command("CONFIG", 0), 0, (1 << SCLK) | (1 << CS) | (1 << MOSI)))
    requests.append(Frame(command("CONFIG", 1), 0, 1 << MISO))
    requests += txpush_requests(bytes([to_target]), engine=0)
    requests += txpush_requests(bytes([to_controller]), engine=1)
    requests.append(Frame(COMMANDS["START"], 0, 0b11))
    await run_setup(chip, requests)
    await await_running(dut, engine=0)

    # Both engines live on one chip and reach each other only through the uio
    # pads: a pad an engine drives reads its own value back on that engine's
    # input, and the other engine reads the same pad. Silicon provides that
    # read-back; the harness models it by looping uio_out (where driven) back
    # into uio_in each cycle, a cycle behind, which the input filter absorbs.
    # Undriven uio pins sit at the board pull level: chip select idles high and
    # SCLK idles at CPOL (low for mode 0). The loopback holds those levels for
    # pins neither engine drives, so engine 1 sees a real CS falling edge.
    pull = 0xFF & ~(1 << SCLK)  # CPOL 0: SCLK idles low
    loopback = pull
    for _ in range(BYTE_CLOCKS):
        await FallingEdge(dut.clk)
        chip.drive_spi(sck=0, cs_n=1, mosi=0)
        dut.uio_in.value = loopback
        await ReadOnly()
        out = int(dut.uio_out.value)
        oe = int(dut.uio_oe.value)
        loopback = (out & oe) | (pull & ~oe & 0xFF)

    await FallingEdge(dut.clk)
    controller_reply = (await transact(chip, [Frame(COMMANDS["RXPOP"], 0, 0)]))[0]
    target_reply = (await transact(chip, [Frame(command("RXPOP", 1), 0, 0)]))[0]
    controller_value = (controller_reply.payload >> 8) & 0xFF
    target_value = (target_reply.payload >> 8) & 0xFF
    assert controller_value == to_controller, (
        f"controller received {controller_value:#04x}, expected {to_controller:#04x}"
    )
    assert target_value == to_target, (
        f"target received {target_value:#04x}, expected {to_target:#04x}"
    )
