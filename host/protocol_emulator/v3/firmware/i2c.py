"""I2C controller, target and bus-clear firmware for the V3 engines.

Every edge on SDA and SCL is open-drain. The firmware drives a line low with
``OE`` enabling the pin's output while the pin-cell value stays zero, and lets a
line float high with ``OE`` disabling the output so the external pull-up sets
the level. No ``OUT``, ``SET`` or ``DRIVE`` ever requests a high drive on SDA or
SCL: the firmware never asks the pin cell to source a one, so a line is only
ever actively pulled low or released. :func:`scan_high_drives` makes that a
checkable property of an assembled image.

All bit timing comes from :data:`protocol_emulator.v3.timing.I2C`, which gives
the ``(low, high)`` SCL cycle counts for each supported rate. No cycle count is
written as a literal here; the only bare constants are the instruction-latency
and input-settling guards, which are documented as such and are far shorter than
any SCL phase.

Controller host-queue framing
------------------------------

:func:`i2c_controller` runs a small byte-stream interpreter. The host pushes a
sequence of operations onto the transmit queue with ``TXPUSH`` and the program
consumes them with ``PULL``. Each operation is one **op byte**, some followed by
one payload byte:

======================= ===== ============ ======================================
Operation               Op    Payload      Effect
======================= ===== ============ ======================================
START                   0x01  addr/dir     START, then clock out the address byte
WRITE                   0x02  data         clock out one data byte
READ                    0x03  --           read one byte, answer ACK
READ_NACK               0x04  --           read one byte, answer NACK
REPEATED START          0x05  addr/dir     repeated START, then the address byte
STOP                    0x06  --           STOP
======================= ===== ============ ======================================

The **address/direction byte** is the 7-bit address in bits 7:1 and the
read/write bit in bit 0 (``0`` write, ``1`` read), exactly as it travels on the
wire. The host composes it; the firmware clocks it out like any other byte. A
typical "write ``0xAB`` to device ``0x50``" transaction is the five bytes
``0x01 0xA0 0x02 0xAB 0x06``.

The host must keep the queue fed for the duration of a transaction, as for the
UART transmitter. A ``PULL`` on an empty queue stalls with SCL held low, which
is a legal (controller-driven) clock stretch; when the byte finally arrives the
engine resumes, one SCL phase having over-run its deadline (the sticky
missed-deadline flag records it). Between transactions the queue may be empty
for any length of time.

Controller receive records
--------------------------

Every operation that exchanges a byte pushes one ``RX`` record, read back with
``RXPOP`` as ``(value, meta)``:

* START, REPEATED START and WRITE push ``value = 0`` and ``meta`` carrying the
  acknowledge result: :data:`RX_META_ACK` (0) when the target pulled SDA low in
  the acknowledge slot, :data:`RX_META_NACK` when it left SDA high, or
  :data:`RX_META_ARB_LOST` when the controller released SDA to send a one but
  read it low -- it lost arbitration, stopped driving, and abandoned the byte.
* READ and READ_NACK push the received byte in ``value`` with ``meta`` flagging
  the direction: :data:`RX_META_READ`, plus :data:`RX_META_LAST` for READ_NACK
  (the controller answered NACK, ending the read).

STOP pushes no record. On a NACK the controller does not act on its own; the
host sees the record and should issue STOP (or a repeated START). On
arbitration loss the controller has already released the bus.

Clock stretching and arbitration
---------------------------------

During every data and acknowledge clock the controller releases SCL and then
confirms the line actually rose before timing the high phase. If a target holds
SCL low (a clock stretch) the controller waits for the real rising edge and
anchors the high phase to it; when no stretch occurs the high phase is anchored
to the scheduled release, so the measured low and high times equal the table
exactly. START, STOP and repeated START use fixed timing and do not wait for a
stretched SCL.

Arbitration is checked on every one bit the controller sends (address or data):
having released SDA, it samples the line while SCL is high, and if another
driver is holding SDA low it records :data:`RX_META_ARB_LOST`, releases SDA and
SCL, and returns to the top of the interpreter without clocking the rest of the
byte.

Target host framing
-------------------

:func:`i2c_target` matches one 7-bit address. It waits for a START, shifts in
the address, and if it matches pulls SDA low to acknowledge; otherwise it leaves
SDA high (NACK) and waits for the next START. On a write it receives bytes and
pushes each with ``RX`` (``meta`` :data:`RX_META_WRITTEN`), acknowledging each.
On a read it pulls bytes from the transmit queue and clocks them out, releasing
SDA for the controller's acknowledge. After acknowledging the address it holds
SCL low for a fixed stretch so a controller that honours stretching waits; this
is the clock-stretch demonstration. A START or STOP at any point re-synchronises
the target.
"""
from typing import TYPE_CHECKING

from .. import PIN_COUNT, timing

if TYPE_CHECKING:
    from ..isa import Image

#: Acknowledge received (SDA low in the acknowledge slot): a clean transfer.
RX_META_ACK = 0x00
#: No acknowledge (SDA high in the acknowledge slot).
RX_META_NACK = 0x01
#: Arbitration lost: a released SDA read back low while sending a one.
RX_META_ARB_LOST = 0x02
#: Receive record carries a byte the controller read from the bus.
RX_META_READ = 0x04
#: The controller answered NACK after this read byte, ending the read.
RX_META_LAST = 0x08
#: Target receive record: a byte written to the target by a controller.
RX_META_WRITTEN = 0x10

#: Controller op-byte values pulled from the transmit queue.
OP_START = 0x01
OP_WRITE = 0x02
OP_READ = 0x03
OP_READ_NACK = 0x04
OP_REPEATED_START = 0x05
OP_STOP = 0x06

#: Ticks added to the cursor at the start of a transaction so the first START
#: edge is scheduled a few instructions into the future rather than applied
#: late. An instruction-latency guard, not a protocol time.
_START_GUARD = 8

#: Ticks after an SCL release at which the line is sampled to read data, confirm
#: the clock rose, and check arbitration. It must exceed the pad-to-engine input
#: latency (two synchroniser flops plus the three-sample filter) and stay well
#: inside the shortest SCL high phase. A settling guard, not a protocol time.
_SAMPLE_DELAY = 16

#: Open-ended wait horizon, in ticks, for the start-edge and stretch waits. The
#: cursor is pushed this far ahead so ``WAITEDGE``/``WAITFOR`` effectively never
#: time out; it is a watchdog window inside the signed scheduling range.
_WAIT_HORIZON = 1 << 20


def _check_pins(sda: int, scl: int) -> None:
    """Validate the SDA and SCL pin indices.

    :param sda: the SDA pin.
    :param scl: the SCL pin.
    :raises ValueError: if a pin is out of range or the two are the same.
    """
    for name, pin in (("sda", sda), ("scl", scl)):
        if not 0 <= pin < PIN_COUNT:
            raise ValueError(f"{name} must be 0..{PIN_COUNT - 1}, got {pin}")
    if sda == scl:
        raise ValueError("sda and scl must be different pins")


def _rate(rate: int) -> tuple[int, int]:
    """Return the ``(low, high)`` SCL cycle counts for ``rate``.

    :param rate: the bus rate in Hz.
    :raises ValueError: if the rate has no entry in :data:`timing.I2C`.
    """
    if rate not in timing.I2C:
        raise ValueError(f"unsupported I2C rate {rate}; choose one of {sorted(timing.I2C)}")
    return timing.I2C[rate]


_CONTROLLER_TEMPLATE = """\
# I2C controller. R0 byte/shift, R1 scratch, R2 sampled pins, R3 status/counter.
# SDA is pin {sda}, SCL is pin {scl}; both open-drain, pulled low by OE only.
main:
    PULL reg=0
    BREQ reg=0 value={op_start} target=h_start
    BREQ reg=0 value={op_rstart} target=h_restart
    BREQ reg=0 value={op_write} target=h_write
    BREQ reg=0 value={op_read} target=h_read
    BREQ reg=0 value={op_readnack} target=h_readnack
    BREQ reg=0 value={op_stop} target=h_stop
    JMP target=main
h_start:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe={sda_mask} offset=0
    ADVANCE ticks={high}
    OE mask={scl_mask} oe={scl_mask} offset=0
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value={arb} target=h_abort
    JMP target=main
h_restart:
    OE mask={sda_mask} oe=0 offset={setup}
    ADVANCE ticks={low}
    OE mask={scl_mask} oe=0 offset=0
    ADVANCE ticks={high}
    OE mask={sda_mask} oe={sda_mask} offset=0
    ADVANCE ticks={high}
    OE mask={scl_mask} oe={scl_mask} offset=0
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value={arb} target=h_abort
    JMP target=main
h_write:
    PULL reg=0
    CALL target=send_byte
    BREQ reg=3 value={arb} target=h_abort
    JMP target=main
h_read:
    CALL target=read_byte
    OE mask={sda_mask} oe={sda_mask} offset={setup}
    CALL target=tick_bit
    RX meta={read}
    JMP target=main
h_readnack:
    CALL target=read_byte
    OE mask={sda_mask} oe=0 offset={setup}
    CALL target=tick_bit
    RX meta={read_last}
    JMP target=main
h_stop:
    OE mask={sda_mask} oe={sda_mask} offset={setup}
    ADVANCE ticks={low}
    OE mask={scl_mask} oe=0 offset=0
    ADVANCE ticks={high}
    OE mask={sda_mask} oe=0 offset=0
    ADVANCE ticks={high}
    JMP target=main
h_abort:
    OE mask={both_mask} oe=0 offset=0
    JMP target=main
# send_byte: clock out R0 MSB first, then read the acknowledge. Pushes one RX
# record and returns the status (0 ACK, NACK, or ARB_LOST) in R3.
send_byte:
    CLEARIS
    LI reg=3 value=8
sb_loop:
    MOV dst=1 src=0
    LI reg=2 value=128
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_zero
    OE mask={sda_mask} oe=0 offset={setup}
    CALL target=tick_bit
    LI reg=1 value={sda_mask}
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_arblost
    JMP target=sb_adv
sb_zero:
    OE mask={sda_mask} oe={sda_mask} offset={setup}
    CALL target=tick_bit
sb_adv:
    SHL dst=0 amount=1
    DJNZ reg=3 target=sb_loop
    OE mask={sda_mask} oe=0 offset={setup}
    CALL target=tick_bit
    LI reg=1 value={sda_mask}
    AND dst=1 src=2
    BREQ reg=1 value=0 target=sb_acked
    LI reg=3 value={nack}
    RX meta={nack}
    RET
sb_acked:
    LI reg=3 value={ack}
    RX meta={ack}
    RET
sb_arblost:
    OE mask={both_mask} oe=0 offset=0
    LI reg=3 value={arb}
    RX meta={arb}
    RET
# read_byte: clock in eight bits MSB first into the input shift register.
read_byte:
    CLEARIS
    LI reg=3 value=8
rb_loop:
    OE mask={sda_mask} oe=0 offset={setup}
    CALL target=tick_bit
    IN pin={sda} msb_first=1
    DJNZ reg=3 target=rb_loop
    RET
# tick_bit: one SCL low/high pulse. SDA is set up by the caller during the low
# phase. Samples the bus into R2 during the high phase and tolerates a target
# that stretches the clock by holding SCL low.
tick_bit:
    ADVANCE ticks={low}
    OE mask={scl_mask} oe=0 offset=0
    ADVANCE ticks={peek}
    WAITTIME
    SAMPLE reg=2
    LI reg=1 value={scl_mask}
    AND dst=1 src=2
    BREQ reg=1 value=0 target=tb_stretch
    ADVANCE ticks={high_rest}
    OE mask={scl_mask} oe={scl_mask} offset=0
    RET
tb_stretch:
    ADVANCE ticks={horizon}
    WAITEDGE pin={scl} polarity=rising
    EDGETIME pin={scl} reg=1
    CURSORSET reg=1
    ADVANCE ticks={peek}
    WAITTIME
    SAMPLE reg=2
    ADVANCE ticks={high_rest}
    OE mask={scl_mask} oe={scl_mask} offset=0
    RET
"""


_BUS_CLEAR_TEMPLATE = """\
# I2C bus clear: nine SCL pulses with SDA released, then a STOP. Recovery only.
# SDA is pin {sda}, SCL is pin {scl}; both open-drain.
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe=0 offset=0
    LI reg=3 value=8
bc_loop:
    OE mask={scl_mask} oe={scl_mask} offset=0
    ADVANCE ticks={low}
    OE mask={scl_mask} oe=0 offset=0
    ADVANCE ticks={high}
    DJNZ reg=3 target=bc_loop
    OE mask={scl_mask} oe={scl_mask} offset=0
    OE mask={sda_mask} oe={sda_mask} offset={setup}
    ADVANCE ticks={low}
    OE mask={scl_mask} oe=0 offset=0
    ADVANCE ticks={high}
    OE mask={sda_mask} oe=0 offset=0
    ADVANCE ticks={high}
    HALT
"""


_TARGET_TEMPLATE = """\
# I2C target at 7-bit address {address}. SDA is pin {sda}, SCL is pin {scl}.
# Matches the address, acknowledges, clock-stretches once, then receives one
# byte (write) or transmits one byte from the queue (read). A START or STOP
# re-synchronises it. R0 byte/scratch, R1 scratch, R2 pins/send, R3 counter.
reset_t:
    OE mask={both_mask} oe=0 offset=0
    CLEARIS
wait_start:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={horizon}
    WAITEDGE pin={sda} polarity=falling
    BRC condition=timeout target=wait_start
    SAMPLE reg=2
    LI reg=1 value={scl_mask}
    AND dst=1 src=2
    BREQ reg=1 value=0 target=wait_start
    CLEARIS
    LI reg=3 value=8
ga_loop:
    CALL target=wait_scl_rise
    IN pin={sda} msb_first=1
    DJNZ reg=3 target=ga_loop
    MOVIS reg=0
    MOV dst=1 src=0
    SHR dst=1 amount=1
    BREQ reg=1 value={address} target=addr_ok
    JMP target=reset_t
addr_ok:
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe={sda_mask} offset=0
    OE mask={scl_mask} oe={scl_mask} offset=0
    ADVANCE ticks={stretch}
    OE mask={scl_mask} oe=0 offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe=0 offset=0
    LI reg=1 value=1
    AND dst=1 src=0
    BREQ reg=1 value=0 target=t_receive
    JMP target=t_transmit
t_receive:
    CLEARIS
    LI reg=3 value=8
tr_loop:
    CALL target=wait_scl_rise
    IN pin={sda} msb_first=1
    DJNZ reg=3 target=tr_loop
    RX meta={written}
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe={sda_mask} offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe=0 offset=0
    JMP target=reset_t
t_transmit:
    PULL reg=2
    LI reg=3 value=8
tt_loop:
    MOV dst=1 src=2
    LI reg=0 value=128
    AND dst=1 src=0
    NOW reg=0
    CURSORSET reg=0
    ADVANCE ticks={guard}
    BREQ reg=1 value=0 target=tt_zero
    OE mask={sda_mask} oe=0 offset=0
    JMP target=tt_next
tt_zero:
    OE mask={sda_mask} oe={sda_mask} offset=0
tt_next:
    CALL target=wait_scl_rise
    SHL dst=2 amount=1
    DJNZ reg=3 target=tt_fall
    JMP target=tt_after
tt_fall:
    CALL target=wait_scl_fall
    JMP target=tt_loop
tt_after:
    CALL target=wait_scl_fall
    NOW reg=0
    CURSORSET reg=0
    ADVANCE ticks={guard}
    OE mask={sda_mask} oe=0 offset=0
    CALL target=wait_scl_rise
    CALL target=wait_scl_fall
    JMP target=reset_t
wait_scl_rise:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={horizon}
    WAITEDGE pin={scl} polarity=rising
    RET
wait_scl_fall:
    NOW reg=1
    CURSORSET reg=1
    ADVANCE ticks={horizon}
    WAITEDGE pin={scl} polarity=falling
    RET
"""


def i2c_controller(sda: int, scl: int, rate: int) -> str:
    """Return assembly for an I2C controller on ``sda``/``scl`` at ``rate``.

    The program runs the op-byte interpreter described in the module docstring.

    :param sda: the SDA protocol pin.
    :param scl: the SCL protocol pin.
    :param rate: the bus rate in Hz, 100000 or 400000.
    :returns: assembly source for :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if a pin is out of range or equal, or the rate is
        unsupported.
    """
    _check_pins(sda, scl)
    low, high = _rate(rate)
    return _CONTROLLER_TEMPLATE.format(
        sda=sda,
        scl=scl,
        sda_mask=1 << sda,
        scl_mask=1 << scl,
        both_mask=(1 << sda) | (1 << scl),
        low=low,
        high=high,
        peek=_SAMPLE_DELAY,
        high_rest=high - _SAMPLE_DELAY,
        setup=low // 2,
        guard=_START_GUARD,
        horizon=_WAIT_HORIZON,
        op_start=OP_START,
        op_write=OP_WRITE,
        op_read=OP_READ,
        op_readnack=OP_READ_NACK,
        op_rstart=OP_REPEATED_START,
        op_stop=OP_STOP,
        ack=RX_META_ACK,
        nack=RX_META_NACK,
        arb=RX_META_ARB_LOST,
        read=RX_META_READ,
        read_last=RX_META_READ | RX_META_LAST,
    )


def i2c_target(sda: int, scl: int, rate: int, address: int) -> str:
    """Return assembly for an I2C target on ``sda``/``scl`` at ``rate``.

    The target matches ``address`` (7-bit), acknowledges, holds SCL low once as
    a clock stretch, then receives one byte (write) or transmits one byte pulled
    from the transmit queue (read). See the module docstring for the framing.

    :param sda: the SDA protocol pin.
    :param scl: the SCL protocol pin.
    :param rate: the bus rate in Hz, used for the clock-stretch duration.
    :param address: the 7-bit address to match, ``0..127``.
    :returns: assembly source for :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if a pin is out of range or equal, the rate is
        unsupported, or the address does not fit in seven bits.
    """
    _check_pins(sda, scl)
    low, high = _rate(rate)
    if not 0 <= address < 128:
        raise ValueError(f"address must be a 7-bit value 0..127, got {address}")
    return _TARGET_TEMPLATE.format(
        sda=sda,
        scl=scl,
        sda_mask=1 << sda,
        scl_mask=1 << scl,
        both_mask=(1 << sda) | (1 << scl),
        address=address,
        stretch=low,
        guard=_START_GUARD,
        horizon=_WAIT_HORIZON,
        written=RX_META_WRITTEN,
    )


def scan_high_drives(image: "Image", sda: int, scl: int) -> list[tuple[int, str]]:
    """Return every instruction in ``image`` that drives SDA or SCL high.

    An open-drain line must only be pulled low or released, so any instruction
    that asks the pin cell to source a one on SDA or SCL is a defect. This scans
    the assembled words and reports ``OUT`` with ``level=1``, ``SET`` with the
    pin's value bit set, and ``DRIVE`` with both the pin's value and output-enable
    bits set, for either pin.

    :param image: an assembled :class:`~protocol_emulator.v3.isa.Image`.
    :param sda: the SDA pin.
    :param scl: the SCL pin.
    :returns: ``(address, detail)`` for each offending instruction; empty when
        the image never requests a high drive on either pin.
    """
    from .. import isa

    offenders: list[tuple[int, str]] = []
    for address, word in enumerate(image.words):
        try:
            instr = isa.decode(word)
        except isa.IllegalInstruction:
            continue
        operands = instr.operands
        for pin in (sda, scl):
            bit = 1 << pin
            if instr.name == "OUT" and operands["pin"] == pin and operands["level"] == 1:
                offenders.append((address, f"OUT drives pin {pin} high"))
            elif instr.name == "SET" and operands["mask"] & bit and operands["value"] & bit:
                offenders.append((address, f"SET drives pin {pin} high"))
            elif instr.name == "DRIVE" and operands["value"] & bit and operands["oe"] & bit:
                offenders.append((address, f"DRIVE drives pin {pin} high"))
    return offenders


def i2c_bus_clear(sda: int, scl: int, rate: int) -> str:
    """Return assembly for the I2C bus-clear recovery on ``sda``/``scl``.

    Nine SCL pulses with SDA released, then a STOP, then ``HALT``. This frees a
    target that is holding SDA low; it is not a data transfer.

    :param sda: the SDA protocol pin.
    :param scl: the SCL protocol pin.
    :param rate: the bus rate in Hz, 100000 or 400000.
    :returns: assembly source for :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if a pin is out of range or equal, or the rate is
        unsupported.
    """
    _check_pins(sda, scl)
    low, high = _rate(rate)
    return _BUS_CLEAR_TEMPLATE.format(
        sda=sda,
        scl=scl,
        sda_mask=1 << sda,
        scl_mask=1 << scl,
        low=low,
        high=high,
        setup=low // 2,
        guard=_START_GUARD,
    )
