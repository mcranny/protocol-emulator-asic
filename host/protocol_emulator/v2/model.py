"""Cycle-level candidate V2 architecture. Inputs here are already synchronized.

The model is an executable specification, not a hardware transport. Host access
methods are atomic operations between edges; pin-driven timing needs RTL tests.
"""
from collections import deque
from dataclasses import dataclass

from ..model import Model as V1Model, ADDRESS, ILLEGAL, OVERFLOW, FRAME
from .isa import decode

OWNERSHIP = 1 << 11
FIRMWARE = 1 << 12


@dataclass(frozen=True)
class Watch:
    mask: int
    value: int
    edges: int

    def __post_init__(self):
        if any(not isinstance(x, int) or not 0 <= x <= 255
               for x in (self.mask, self.value, self.edges)) or self.value & ~self.mask:
            raise ValueError("invalid pin watch")


class Engine(V1Model):
    def __init__(self, capacity=128, depth=16):
        if capacity not in (64, 128) or depth != 16:
            raise ValueError("unsupported feasibility configuration")
        self.capacity, self.depth = capacity, depth
        self.owner = self.open_drain = 0
        self.watches = ()
        self.abort_mask = 0
        super().__init__()
        self.program = [None] * capacity

    def reset_state(self):
        super().reset_state()
        self.rx = deque()
        self.isr = self.events = self.marker = 0
        self.timeout = self.interrupted = self.pull_empty = False
        self.previous_pins = 0
        self.firmware_error = 0
        self.links = []

    @property
    def pins(self):
        enabled = self.oe & self.owner if self.running else 0
        enabled &= ~(self.open_drain & self.out)
        return self.out & ~self.open_drain & self.owner, enabled

    def load(self, words):
        if self.running:
            raise ValueError("stop before loading")
        if not 1 <= len(words) <= self.capacity:
            raise ValueError("invalid program length")
        for word in words:
            insn = decode(word)
            if insn.name in ("JMP", "BRPIN", "BREQ", "BRC", "BRDIFF", "CALL", "WAITBR"):
                target = insn.operands[0]
            elif insn.name == "DJNZ":
                target = insn.operands[1]
            else:
                continue
            if target >= self.capacity:
                raise ValueError("branch outside memory")
        self.program = list(words) + [0xC00000] * (self.capacity - len(words))

    def start(self):
        if self.running or self.program[0] is None:
            raise ValueError("invalid start")
        tx, rx, errors = self.fifo, self.rx, self.errors
        previous = self.previous_pins
        self.reset_state()
        self.fifo, self.rx, self.errors = tx, rx, errors
        self.previous_pins = previous
        self.running = True

    def push(self, data):
        data = bytes(data)
        if len(self.fifo) + len(data) > self.depth:
            raise BufferError("TX FIFO full; no bytes committed")
        self.fifo.extend(data)

    def read(self, count):
        if not isinstance(count, int) or not 0 <= count <= self.depth:
            raise ValueError("invalid RX count")
        return [self.rx.popleft() for _ in range(min(count, len(self.rx)))]

    def advance(self, target=None):
        if target is not None:
            if not 0 <= target < self.capacity:
                self.fault(ADDRESS)
            else:
                self.pc = target
        elif self.pc == self.capacity - 1:
            self.fault(ADDRESS)
        else:
            self.pc += 1

    def tick(self, pins=0):
        self.cycle += 1
        self.marker = 0
        for index, watch in enumerate(self.watches):
            if pins & watch.mask == watch.value and (pins ^ self.previous_pins) & watch.edges:
                self.events |= 1 << index
        self.previous_pins = pins
        if self.running:
            self.execute(pins)

    def execute(self, pins):
        try:
            insn = decode(self.program[self.pc])
        except (ValueError, TypeError):
            self.fault(ILLEGAL)
            return
        name, a = insn.name, insn.operands
        if name == "IN":
            bit = pins >> a[0] & 1
            self.isr = ((self.isr << 1 | bit) & 255) if a[1] else (self.isr >> 1 | bit << 7)
        elif name == "RX":
            if len(self.rx) == self.depth:
                self.fault(OVERFLOW)
                return
            self.rx.append((self.isr, a[0]))
        elif name == "CLEARIS":
            self.isr = 0
        elif name == "BRPIN":
            self.advance(a[0] if pins >> a[1] & 1 == a[2] else None)
            return
        elif name == "BREQ":
            self.advance(a[0] if self.regs[a[2]] == a[1] else None)
            return
        elif name == "BRC":
            conditions = (not self.fifo, len(self.rx) == self.depth, self.timeout,
                          self.interrupted, self.events & 1, self.events & 2, self.pull_empty)
            self.advance(a[0] if conditions[a[1]] else None)
            return
        elif name == "DRIVE":
            if a[1] & ~self.owner:
                self.fault(OWNERSHIP)
                return
            self.out, self.oe = a
        elif name in ("WAITFOR", "WAITEVENT", "WAITBR"):
            remaining = self.wait_left or self.regs[1]
            if not remaining:
                self.fault(ILLEGAL)
                return
            if name == "WAITBR":
                matched = pins >> a[1] & 1 == a[2]
            elif name == "WAITFOR":
                matched = pins >> a[0] & 1 == a[1]
            else:
                matched = bool(self.events & a[0])
            aborted = bool(self.events & self.abort_mask) if name != "WAITEVENT" else False
            if matched or aborted or remaining == 1:
                self.timeout = not matched and not aborted
                self.interrupted = aborted
                self.wait_left = 0
                self.advance(a[0] if name == "WAITBR" and (aborted or self.timeout) else None)
            else:
                self.wait_left = remaining - 1
            return
        elif name == "MARK":
            self.marker = a[0]
        elif name == "MOVIS":
            self.regs[a[0]] = self.isr
        elif name == "OUT8":
            self.out = self.out & ~(1 << a[0]) | (self.osr >> 7 & 1) << a[0]
            self.osr = self.osr << 1 & 255
        elif name == "PULLNB":
            self.pull_empty = not self.fifo
            if self.fifo:
                self.regs[a[0]] = self.fifo.popleft()
        elif name == "CLEAREVENT":
            self.events &= ~a[0]
        elif name == "FAIL":
            self.firmware_error |= a[0]
            self.fault(FIRMWARE)
            return
        elif name == "BRDIFF":
            self.advance(a[0] if (pins ^ self.out) >> a[1] & 1 else None)
            return
        elif name == "CALL":
            if len(self.links) == 2 or self.pc == self.capacity - 1:
                self.fault(ILLEGAL)
                return
            self.links.append(self.pc + 1)
            self.advance(a[0])
            return
        elif name == "RET":
            if not self.links:
                self.fault(ILLEGAL)
                return
            target = self.links.pop()
            self.advance(target)
            return
        elif name == "UNLINK":
            self.links.clear()
        elif name == "JMP":
            self.advance(a[0])
            return
        elif name == "DJNZ":
            self.regs[a[0]] = self.regs[a[0]] - 1 & 65535
            self.advance(a[1] if self.regs[a[0]] else None)
            return
        elif name == "OE" and a[0] & a[1] & ~self.owner:
            self.fault(OWNERSHIP)
            return
        else:
            super().execute(pins)
            return
        self.advance()


class Device:
    def __init__(self, capacity=128):
        self.engines = [Engine(capacity), Engine(capacity)]
        self.cycle = 0

    def configure(self, owners, open_drain=(0, 0)):
        if any(e.running for e in self.engines):
            raise ValueError("both engines must be stopped")
        if len(owners) != 2 or len(open_drain) != 2:
            raise ValueError("two engine masks required")
        if any(not isinstance(x, int) or not 0 <= x <= 255 for x in (*owners, *open_drain)):
            raise ValueError("invalid mask")
        if owners[0] & owners[1] or any(od & ~own for od, own in zip(open_drain, owners)):
            raise ValueError("conflicting ownership or unowned open-drain pin")
        for engine, own, od in zip(self.engines, owners, open_drain):
            engine.owner, engine.open_drain = own, od

    def start(self, mask):
        if not isinstance(mask, int) or not 1 <= mask <= 3:
            raise ValueError("invalid engine mask")
        selected = [e for i, e in enumerate(self.engines) if mask >> i & 1]
        if any(e.running or e.program[0] is None for e in selected):
            raise ValueError("invalid atomic start")
        for engine in selected:
            engine.start()

    def tick(self, pins=0, *, enable=True, reset=False, frame_error=False):
        self.cycle += 1
        for engine in self.engines:
            if reset:
                engine.program = [None] * engine.capacity
                engine.reset_state()
                engine.errors = engine.owner = engine.open_drain = 0
                engine.watches, engine.abort_mask = (), 0
            elif not enable:
                engine.reset_state()
            elif frame_error:
                engine.fault(FRAME)
            else:
                engine.tick(pins)
        return self.pins

    @property
    def pins(self):
        values, enables = zip(*(engine.pins for engine in self.engines))
        # Values from a non-driving owner must not contaminate another pin.
        return (values[0] & enables[0]) | (values[1] & enables[1]), enables[0] | enables[1]
