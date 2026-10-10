"""A four-bit pulse-width protocol implemented entirely with the V2 ISA.

Pin 0 idles low. A 40-cycle high sync pulse and 20-cycle low gap precede four
MSB-first bits: a 10-cycle high pulse is zero, a 20-cycle high pulse is one,
and each bit is followed by 10 low cycles. RX metadata 0x50 identifies a frame.
At 25 MHz these intervals are 1.6 us, 800 ns, 400 ns and 800 ns respectively.
"""
from .isa import assemble
from .replay import OutputEvent, compile_schedule


def transmit(nibble):
    if type(nibble) is not int or not 0 <= nibble < 16:
        raise ValueError('expected a four-bit payload')
    events = [OutputEvent(0, 0, 1), OutputEvent(4, 1, 1), OutputEvent(44, 0, 1)]
    cycle = 64
    for bit in range(3, -1, -1):
        events.append(OutputEvent(cycle, 1, 1))
        cycle += 20 if nibble >> bit & 1 else 10
        events.append(OutputEvent(cycle, 0, 1))
        cycle += 10
    return compile_schedule(events, end_cycle=cycle + 4, owner=1)


def receive():
    # The firmware samples 14 clocks after each observed data rising edge,
    # separating the two legal pulse widths despite synchronizer phase.
    return assemble('''LI 1, 1000
WAITFOR 0, 1
BRC timeout, 2
WAITFOR 0, 0
BRC timeout, 2
CLEARIS
LI 0, 4
bit: WAITFOR 0, 1
BRC timeout, 2
DELAY 12
IN 0, 1
WAITFOR 0, 0
BRC timeout, 2
DJNZ 0, bit
RX 80
HALT
timeout: FAIL 2''', capacity=64)


def decode(events):
    """Independent width decoder for (cycle, pin-level) observations."""
    widths, rising, previous = [], None, 0
    for cycle, level in events:
        level &= 1
        if level and not previous: rising = cycle
        elif previous and not level:
            if rising is None: raise ValueError('missing pulse start')
            widths.append(cycle - rising)
        previous = level
    if len(widths) != 5 or widths[0] != 40 or any(width not in (10, 20) for width in widths[1:]):
        raise ValueError('invalid pulse-width frame')
    return sum((width == 20) << (3 - i) for i, width in enumerate(widths[1:]))
