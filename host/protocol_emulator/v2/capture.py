"""Bounded post-trigger capture reference contract.

Sample immediately before engine execution at an edge. Output/marker changes
from that execution therefore appear at the next capture sample. Timestamps
are cycles since ARM, not asynchronous stimulus edge timestamps.
"""
from dataclasses import asdict, dataclass


TRIGGER = 8
ERROR = 4
MARKERS = 3


@dataclass(frozen=True)
class Sample:
    inputs: int
    outputs: int
    enables: int
    flags: int = 0

    def __post_init__(self):
        if any(type(value) is not int or not 0 <= value <= 255
               for value in (self.inputs, self.outputs, self.enables, self.flags)):
            raise ValueError("capture fields must be bytes")
        if self.flags & TRIGGER:
            raise ValueError("trigger flag is reserved for capture")


@dataclass(frozen=True)
class Record:
    cycle: int
    inputs: int
    outputs: int
    enables: int
    flags: int

    @property
    def packed(self):
        return (self.cycle << 32 | self.inputs << 24 | self.outputs << 16
                | self.enables << 8 | self.flags)


class Capture:
    def __init__(self, depth=32, timestamp_bits=32):
        if type(depth) is not int or not 1 <= depth <= 32:
            raise ValueError("capture depth must be 1..32")
        if type(timestamp_bits) is not int or not 1 <= timestamp_bits <= 32:
            raise ValueError("timestamp width must be 1..32")
        self.depth, self.maximum = depth, (1 << timestamp_bits) - 1
        self.reset()

    def reset(self):
        self.records = []
        self.cycle = 0
        self.armed = self.triggered = self.truncated = False
        self.trigger_cycle = None
        self.reason = "reset"
        self.previous = None
        self.mode, self.mask = "immediate", 255

    def arm(self, initial, mode="immediate", mask=255):
        if not isinstance(initial, Sample):
            raise ValueError("initial capture sample required")
        if mode not in ("immediate", "pin", "marker", "error"):
            raise ValueError("invalid capture trigger")
        if type(mask) is not int or not 0 <= mask <= 255:
            raise ValueError("invalid capture trigger mask")
        self.reset()
        self.armed = True
        self.reason = "armed"
        self.mode, self.mask = mode, mask
        self._sample(initial)

    def _sample(self, sample):
        trigger = not self.triggered and (
            self.mode == "immediate"
            or (self.mode == "pin" and self.previous is not None
                and bool((sample.inputs ^ self.previous.inputs) & self.mask))
            or (self.mode == "marker" and bool(sample.flags & self.mask & MARKERS))
            or (self.mode == "error" and bool(sample.flags & ERROR)))
        if trigger:
            self.triggered = True
            self.trigger_cycle = self.cycle
            self.reason = "capturing"
        # Initial trigger state is always recorded, including an unchanged bus.
        # Later records capture changes and both assertion/deassertion of flags.
        clear_trigger = bool(self.records and self.records[-1].flags & TRIGGER)
        if self.triggered and (trigger or sample != self.previous or clear_trigger or sample.flags & MARKERS):
            self.records.append(Record(self.cycle, sample.inputs, sample.outputs,
                                       sample.enables, sample.flags | (TRIGGER if trigger else 0)))
            if len(self.records) == self.depth:
                self.armed = False
                self.truncated = True
                self.reason = "capacity"
        self.previous = sample

    def tick(self, sample):
        if not isinstance(sample, Sample):
            raise ValueError("capture sample required")
        if not self.armed:
            return
        self.cycle += 1
        self._sample(sample)
        if self.cycle == self.maximum and self.armed:
            self.armed = False
            self.truncated = True
            self.reason = "timestamp"

    def stop(self):
        if self.armed:
            self.armed = False
            self.reason = "stopped" if self.triggered else "untriggered"

    def disable(self):
        if self.armed:
            self.armed = False
            self.reason = "disabled"

    @property
    def complete(self):
        return self.triggered and not self.armed and not self.truncated and self.reason == "stopped"

    def export(self, *, allow_incomplete=False):
        if not allow_incomplete and not self.complete:
            raise ValueError("capture is incomplete")
        return {"schema": 1, "clock_hz": 25_000_000,
                "sampling": "before_execution", "timestamp_origin": "arm",
                "maximum_cycle": self.maximum, "end_cycle": self.cycle,
                "trigger_cycle": self.trigger_cycle, "complete": self.complete,
                "truncated": self.truncated, "reason": self.reason,
                "records": [asdict(record) for record in self.records]}

    def vcd(self):
        """Waveform view only; complete JSON scenarios are the replay source."""
        lines = ["$timescale 1 ns $end",
                 f"$comment complete={self.complete} truncated={self.truncated} reason={self.reason} $end",
                 "$scope module capture $end"]
        signals = (("inputs", "!"), ("outputs", '"'), ("enables", "#"), ("flags", "$"))
        lines += [f"$var wire 8 {identifier} {name} $end" for name, identifier in signals]
        lines += ["$upscope $end", "$enddefinitions $end"]
        for record in self.records:
            lines.append(f"#{record.cycle * 40}")
            lines += [f"b{getattr(record, name):08b} {identifier}" for name, identifier in signals]
        if self.records and self.cycle > self.records[-1].cycle:
            lines.append(f"#{self.cycle * 40}")
        return "\n".join(lines) + "\n"
