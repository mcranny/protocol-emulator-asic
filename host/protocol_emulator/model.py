"""Independent executable architectural model (one step per system edge).

Pin input is already synchronized at this boundary. SPI framing is modeled by
the transport separately. Host commands and execution share the step edge.
"""
from collections import deque
from .isa import decode

VERSION = 0x010140
ILLEGAL, ADDRESS, BUSY, COMMAND, FRAME, OVERFLOW, TIMEOUT = (1 << n for n in range(4, 11))


class Model:
    def __init__(self):
        self.program = [None] * 64
        self.errors = 0
        self.cycle = 0
        self.reset_state()

    def reset_state(self):
        self.running = False
        self.pc = 0
        self.regs = [0, 0]
        self.osr = 0
        self.out = self.oe = 0
        self.delay_left = self.wait_left = 0
        self.fifo = deque()

    def fault(self, code):
        self.errors |= code
        self.running = False

    @property
    def pins(self):
        return (self.out, self.oe if self.running else 0)

    @property
    def status(self):
        return (int(self.running) | int(not self.running) << 1 |
                int(not self.fifo) << 2 | int(len(self.fifo) == 4) << 3 |
                self.errors | len(self.fifo) << 16)

    def snapshot(self):
        return dict(pc=self.pc, running=int(self.running), r0=self.regs[0],
                    r1=self.regs[1], osr=self.osr, out=self.out,
                    oe=self.oe, level=len(self.fifo), errors=self.errors,
                    delay_left=self.delay_left, wait_left=self.wait_left)

    def step(self, pins=0, command=None, frame_error=False, reset=False, enable=True):
        self.cycle += 1
        if reset:
            self.program = [None] * 64
            self.errors = 0
            self.reset_state()
            return None
        if not enable:
            self.reset_state()
            return None
        if frame_error:
            self.fault(FRAME)
            return None
        response = None
        pending_push = None
        control = False
        if command is not None:
            cmd, addr, data = command
            result, error = 0, 0
            if not 0 <= cmd <= 9 or not 0 <= addr <= 255 or not 0 <= data <= 0xFFFFFF:
                error = COMMAND
            elif cmd not in (1, 2) and addr or cmd not in (1, 7) and data or cmd == 7 and data > 255:
                error = COMMAND
            elif cmd in (1, 2) and addr >= 64:
                error = ADDRESS
            elif cmd == 1:
                if self.running:
                    error = BUSY
                else:
                    try:
                        decode(data)
                        self.program[addr] = data
                        result = data
                    except ValueError:
                        error = ILLEGAL
            elif cmd == 2:
                if self.program[addr] is None:
                    error = ILLEGAL
                else:
                    result = self.program[addr]
            elif cmd == 3:
                if self.running:
                    error = BUSY
                elif self.program[0] is None:
                    error = ILLEGAL
                else:
                    saved_fifo = self.fifo
                    self.reset_state()
                    self.fifo = saved_fifo
                    self.running = True
                    control = True
            elif cmd == 4:
                self.running = False
                control = True
            elif cmd == 5:
                self.reset_state()
                self.errors = 0
                control = True
            elif cmd == 6:
                result = self.status
            elif cmd == 7:
                if len(self.fifo) == 4:
                    error = OVERFLOW
                else:
                    pending_push = data
                    result = len(self.fifo) + 1
            elif cmd == 8:
                result = len(self.fifo)
            elif cmd == 9:
                result = VERSION
            if error:
                self.errors |= error
                if error not in (BUSY, OVERFLOW):
                    self.running = False
            response = ((cmd | (0x80 if error else 0)) & 255, addr & 255, result)
        if self.running and not control:
            self.execute(pins)
        if pending_push is not None:
            self.fifo.append(pending_push)
        return response

    def advance(self, target=None):
        if target is not None:
            self.pc = target
        elif self.pc == 63:
            self.fault(ADDRESS)
        else:
            self.pc += 1

    def execute(self, pins):
        word = self.program[self.pc]
        try:
            if word is None:
                raise ValueError("unwritten")
            instruction = decode(word)
        except ValueError:
            self.fault(ILLEGAL)
            return
        name, a = instruction.name, instruction.operands
        if name == "DELAY":
            if self.delay_left:
                self.delay_left -= 1
                if not self.delay_left:
                    self.advance()
            elif a[0] == 1:
                self.advance()
            else:
                self.delay_left = a[0] - 1
            return
        if name == "WAIT":
            remaining = self.wait_left or a[2]
            if (pins >> a[0]) & 1 == a[1]:
                self.wait_left = 0
                self.advance()
            elif remaining == 1:
                self.wait_left = 0
                self.fault(TIMEOUT)
            else:
                self.wait_left = remaining - 1
            return
        if name == "PULL":
            if not self.fifo:
                return
            self.regs[a[0]] = self.fifo.popleft()
        elif name == "SET":
            self.out = (self.out & ~a[0]) | (a[1] & a[0])
        elif name == "OE":
            self.oe = (self.oe & ~a[0]) | (a[1] & a[0])
        elif name == "LI":
            self.regs[a[0]] = a[1]
        elif name == "MOVOS":
            self.osr = self.regs[a[0]]
        elif name == "OUT":
            value = self.osr >> 15 if a[1] else self.osr & 1
            self.out = self.out & ~(1 << a[0]) | value << a[0]
            self.osr = (self.osr << 1) & 65535 if a[1] else self.osr >> 1
        elif name == "SAMPLE":
            self.regs[a[0]] = pins & a[1]
        elif name == "DJNZ":
            self.regs[a[0]] = (self.regs[a[0]] - 1) & 65535
            self.advance(a[1] if self.regs[a[0]] else None)
            return
        elif name == "JMP":
            self.advance(a[0])
            return
        elif name == "HALT":
            self.running = False
            return
        self.advance()
