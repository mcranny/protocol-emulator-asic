"""UART transmit and receive firmware for the V3 engines.

Both programs implement 8N1 framing (one low start bit, eight data bits least
significant first, one high stop bit) on a single protocol pin. Every timing
constant is taken from :mod:`protocol_emulator.v3.timing`; no bit period is
written as a literal here, so a program is correct for any baud that the 48 MHz
clock can represent and assembly fails for one it cannot.

Transmit
--------

:func:`uart_tx` drives one pin and sends one byte per host transmit-queue
entry. Each frame re-anchors the engine cursor to the current time with
``NOW``/``CURSORSET`` and schedules every edge at ``cursor + guard`` so the
start bit lands a few ticks in the future even when the queue byte arrives late
(``PULL`` may have stalled for an arbitrary time). The depth-one pending slot of
the pin cell throttles the loop to the bit rate: the ``OUT`` at the head of each
bit stalls until the previous bit's commit fires, so no synchronization
instruction is needed inside the frame.

The data bit is selected by a branch because ``OUT`` takes a literal level, and
the two branches are not the same length (ten cycles for a one bit, nine for a
zero). That asymmetry cannot jitter the waveform: both branches advance the
cursor by exactly one bit period and every edge time is ``cursor + guard``, so
the edge positions depend only on the cursor, never on how many instructions a
branch took to reach them.

Receive
-------

:func:`uart_rx` samples one pin and never drives it. It waits for the falling
start edge with ``WAITEDGE``, reads the edge timestamp with ``EDGETIME``, and
sets the cursor to ``edge + 1.5`` bit periods so the first data bit is sampled
at its centre. It then samples the eight data bits at one-bit-period steps with
``IN`` (least significant first), reaching the stop bit at ``edge + 9.5`` bit
periods. Because every sample time is derived from the hardware edge timestamp
rather than from counting instructions, the constant pad-to-engine input
latency (the input filter plus the pin cell's synchronizer) cancels: the level
the engine reads is delayed by exactly the same amount as the timestamp it
measured from.

Receive record encoding
-----------------------

Each received frame is pushed with ``RX`` as a ``(value, meta)`` record:

* ``value`` is the received byte, the eight data bits with the first-received
  (least significant) bit in bit 0.
* ``meta`` flags framing. Bit 0 is the framing-error flag: it is ``0`` when the
  stop bit was high (a clean frame) and ``1`` when the stop bit was low (a
  framing error, and the byte in ``value`` is suspect). Bits 7:1 are reserved
  and always zero.
"""
from .. import PIN_COUNT, timing

#: Framing-error flag of the receive record meta byte: stop bit low.
RX_META_OK = 0x00
RX_META_FRAMING = 0x01

#: Offset, in clock ticks, added to the transmit cursor for every scheduled
#: edge. It covers the few instructions between re-anchoring the cursor and the
#: start-bit ``OUT`` so that edge is always scheduled in the future rather than
#: applied late. It is an instruction-latency guard, not a protocol time, and is
#: far smaller than one bit period at every supported baud.
_TX_EDGE_GUARD = 4

#: Horizon, in clock ticks, for the receive start-edge wait. ``WAITEDGE`` times
#: out when the cursor is reached, so the cursor is set this far ahead to make
#: the wait effectively open-ended; on a timeout the program simply re-arms.
#: It is a watchdog window, well inside the signed scheduling range.
_RX_EDGE_WAIT_HORIZON = 1 << 20

_TX_TEMPLATE = """\
# UART transmit, 8N1. One host transmit-queue byte per frame.
# R0 data shift register, R1 bit counter, R2 time scratch, R3 the constant one.
start:
    PULL reg=0
    LI reg=1 value=8
    LI reg=3 value=1
    NOW reg=2
    CURSORSET reg=2
    OUT pin={pin} level=0 offset={guard}
    ADVANCE ticks={bit}
bit:
    MOV dst=2 src=0
    AND dst=2 src=3
    BREQ reg=2 value=0 target=low
    OUT pin={pin} level=1 offset={guard}
    JMP target=after
low:
    OUT pin={pin} level=0 offset={guard}
after:
    SHR dst=0 amount=1
    ADVANCE ticks={bit}
    DJNZ reg=1 target=bit
    OUT pin={pin} level=1 offset={guard}
    ADVANCE ticks={bit}
    WAITTIME
    JMP target=start
"""

_RX_TEMPLATE = """\
# UART receive, 8N1. The engine samples {pin} and never drives it.
# R0 pin mask scratch, R1 bit counter, R2 edge timestamp / time scratch,
# R3 the sampled stop-bit byte. The byte accumulates in the input shift
# register and is pushed by RX; its meta byte flags framing.
rearm:
    CLEARIS
    NOW reg=2
    CURSORSET reg=2
    ADVANCE ticks={horizon}
    WAITEDGE pin={pin} polarity=falling
    BRC target=rearm condition=timeout
    EDGETIME pin={pin} reg=2
    CURSORSET reg=2
    ADVANCE ticks={mid0}
    LI reg=1 value=8
sample:
    WAITTIME
    IN pin={pin} msb_first=0
    ADVANCE ticks={bit}
    DJNZ reg=1 target=sample
    WAITTIME
    SAMPLE reg=3
    LI reg=0 value={mask}
    AND dst=3 src=0
    BREQ reg=3 value=0 target=framing
    RX meta={meta_ok}
    JMP target=rearm
framing:
    RX meta={meta_framing}
    JMP target=rearm
"""


def _check_pin(pin: int) -> None:
    """Validate a protocol pin index.

    :param pin: the pin to drive or sample.
    :raises ValueError: if ``pin`` is outside ``0..PIN_COUNT - 1``.
    """
    if not 0 <= pin < PIN_COUNT:
        raise ValueError(f"pin must be 0..{PIN_COUNT - 1}, got {pin}")


def uart_tx(pin: int, baud: int) -> str:
    """Return assembly for an 8N1 UART transmitter on ``pin`` at ``baud``.

    The program sends one byte per host transmit-queue entry; the host keeps the
    queue fed. See the module docstring for the framing and the time-cursor
    throttling.

    :param pin: the protocol pin to drive, ``0..PIN_COUNT - 1``.
    :param baud: the bit rate in bits per second.
    :returns: assembly source accepted by :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if ``pin`` is out of range, or ``baud`` cannot be
        represented at the system clock within the default rounding tolerance.
    """
    _check_pin(pin)
    bit = timing.bit_cycles(baud)
    return _TX_TEMPLATE.format(pin=pin, bit=bit, guard=_TX_EDGE_GUARD)


def uart_rx(pin: int, baud: int) -> str:
    """Return assembly for an 8N1 UART receiver on ``pin`` at ``baud``.

    The program waits for a start edge, samples eight data bits at their centres
    and the stop bit, then pushes a ``(value, meta)`` record with ``RX``. See
    the module docstring for the record encoding.

    :param pin: the protocol pin to sample, ``0..PIN_COUNT - 1``.
    :param baud: the bit rate in bits per second.
    :returns: assembly source accepted by :func:`protocol_emulator.v3.isa.assemble`.
    :raises ValueError: if ``pin`` is out of range, or ``baud`` cannot be
        represented at the system clock within the default rounding tolerance.
    """
    _check_pin(pin)
    bit = timing.bit_cycles(baud)
    mid0 = bit + bit // 2
    return _RX_TEMPLATE.format(
        pin=pin,
        bit=bit,
        mid0=mid0,
        mask=1 << pin,
        horizon=_RX_EDGE_WAIT_HORIZON,
        meta_ok=RX_META_OK,
        meta_framing=RX_META_FRAMING,
    )
