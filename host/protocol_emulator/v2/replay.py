"""Compile bounded pin waveforms into ordinary V2 DRIVE/DELAY/HALT firmware.

Cycle zero is the first execution edge after START. Each event describes the
visible outputs immediately after that edge. HALT releases pins at end_cycle+1.
Input observations and marker flags are deliberately not playback instructions.
"""
from dataclasses import dataclass

from .isa import encode


@dataclass(frozen=True)
class OutputEvent:
    cycle: int
    outputs: int
    enables: int


def compile_schedule(events, *, end_cycle, owner, open_drain=0, capacity=64):
    """Return words or reject the entire schedule before emitting firmware.

    Released pins must have output value zero, matching the top-level capture
    representation. Open-drain high drive is rejected rather than silently
    converted into a release. The first event must explicitly state cycle zero.
    """
    if capacity not in (64, 128):
        raise ValueError("unsupported program capacity")
    if any(type(x) is not int or not 0 <= x <= 255 for x in (owner, open_drain)):
        raise ValueError("invalid ownership mask")
    if open_drain & ~owner:
        raise ValueError("open-drain pins must be owned")
    if type(end_cycle) is not int or not 0 <= end_cycle <= 0xFFFFFFFF:
        raise ValueError("invalid end cycle")
    events = list(events)
    if not events or not isinstance(events[0], OutputEvent) or events[0].cycle != 0:
        raise ValueError("explicit cycle-zero output state required")
    previous = -1
    required = 1  # final HALT
    for event in events:
        if not isinstance(event, OutputEvent):
            raise ValueError("OutputEvent required")
        if type(event.cycle) is not int or not previous < event.cycle <= end_cycle:
            raise ValueError("events require strictly increasing integer cycles within the window")
        if any(type(x) is not int or not 0 <= x <= 255 for x in (event.outputs, event.enables)):
            raise ValueError("output fields must be bytes")
        if (event.enables & ~owner or event.outputs & ~event.enables
                or event.outputs & event.enables & open_drain):
            raise ValueError("schedule violates ownership, released-pin values or open-drain drive")
        gap = event.cycle - previous - 1
        required += 1 + (gap + 65534) // 65535
        previous = event.cycle
    required += (end_cycle - previous + 65534) // 65535
    if required > capacity:
        raise ValueError(f"schedule requires {required} instructions; capacity is {capacity}")

    words = []
    def delay(cycles):
        while cycles:
            step = min(cycles, 65535)
            words.append(encode("DELAY", step))
            cycles -= step

    previous = -1
    for event in events:
        delay(event.cycle - previous - 1)
        words.append(encode("DRIVE", event.outputs, event.enables))
        previous = event.cycle
    delay(end_cycle - previous)
    words.append(encode("HALT"))
    return words
