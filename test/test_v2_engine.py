"""V2 candidate core differential checks; no physical acceptance claim."""
import json
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer

from protocol_emulator.v2.isa import assemble, encode
from protocol_emulator.v2.model import Engine
from protocol_emulator.v2.firmware import uart_rx, uart_tx, spi_controller, spi_target, i2c_controller, i2c_target
from protocol_emulator.model import BUSY, FRAME, ILLEGAL


class Harness:
    def __init__(self, dut):
        self.d = dut
        self.e = Engine(capacity=64)
        self.e.owner = 255
        self.history = []

    async def step(self, pins=0, events=0, tx=None, pop=False, start=False, stop=False,
                   reset=False, state_reset=False, enable=True, frame=False, write=None):
        d, e = self.d, self.e
        d.clk.value = 0
        d.rst_n.value = not reset
        d.ena.value = enable
        d.pins_in.value = pins
        d.owner.value = e.owner
        d.open_drain.value = e.open_drain
        d.event_set.value = events
        d.abort_mask.value = e.abort_mask
        d.start.value, d.stop.value = start, stop
        d.state_reset.value, d.external_fault.value = state_reset, frame
        d.program_write.value = write is not None
        d.program_addr.value, d.program_data.value = write or (0, 0)
        tx_bytes = ([tx] if isinstance(tx, int) else list(tx)) if tx is not None else []
        d.tx_valid.value = tx is not None
        d.tx_data.value = sum(value << (8 * i) for i, value in enumerate(tx_bytes))
        d.tx_count.value = len(tx_bytes) or 1
        d.rx_pop.value = pop
        await Timer(20, unit="ns")
        pre_tx, pre_rx = len(e.fifo), len(e.rx)
        if reset:
            e.program = [None] * 64
            e.reset_state()
            e.errors = 0
        elif state_reset or not enable:
            e.reset_state()
            if enable:
                e.errors = 0
        else:
            e.marker = 0
            e.events |= events
            if write:
                addr, word = write
                if e.running:
                    e.errors |= BUSY
                else:
                    e.program[addr] = word
            if frame:
                e.fault(FRAME)
            elif stop:
                e.running = False
            elif start:
                if e.running:
                    e.errors |= BUSY
                elif e.program[0] is None:
                    e.errors |= ILLEGAL
                else:
                    e.start()
            else:
                e.tick(pins)
            if tx is not None and pre_tx + len(tx_bytes) <= 16:
                e.fifo.extend(tx_bytes)
            if pop and pre_rx:
                e.rx.popleft()
        d.clk.value = 1
        await Timer(20, unit="ns")
        expected = dict(pc=e.pc, r0=e.regs[0], r1=e.regs[1], osr=e.osr, isr=e.isr,
                        out_value=e.out, out_enable=e.oe, events=e.events, marker=e.marker,
                        timed_out=int(e.timeout), interrupted=int(e.interrupted),
                        pull_empty=int(e.pull_empty), firmware_error=e.firmware_error,
                        running=int(e.running), errors=e.errors, delay_left=e.delay_left,
                        wait_left=e.wait_left, tx_level=len(e.fifo), rx_level=len(e.rx),
                        link_depth=len(e.links), pins_out=e.pins[0],
                        pins_oe=e.pins[1] if enable and not reset else 0)
        actual = {key: int(getattr(d, key).value) for key in expected}
        rx_expected = (e.rx[0][0] | e.rx[0][1] << 8) if e.rx else 0
        record = dict(cycle=e.cycle, pins=pins, events=events, tx=tx, pop=pop,
                      expected=expected, actual=actual)
        self.history.append(record)
        if actual != expected or int(d.rx_data.value) != rx_expected:
            Path("output").mkdir(exist_ok=True)
            Path("output/v2-divergence.json").write_text(json.dumps({
                "program": e.program, "history": self.history[-128:],
                "rx_expected": rx_expected, "rx_actual": int(d.rx_data.value),
            }, indent=2))
        assert actual == expected, record
        assert int(d.rx_data.value) == rx_expected

    async def load(self, source):
        await self.step(reset=True)
        words = assemble(source, capacity=64)
        for address, word in enumerate(words + [encode("HALT")] * (64 - len(words))):
            await self.step(write=(address, word))


@cocotb.test()
async def v2_uart_differential(dut):
    h = Harness(dut)
    for period in (25, 217):
        await h.load(uart_rx(period))
        await h.step(start=True, pins=2)
        for _ in range(10):
            await h.step(pins=2)
        for value in (0, 0x55, 0xA5, 0xFF):
            for bit in [0] + [value >> i & 1 for i in range(8)] + [1]:
                for _ in range(period):
                    await h.step(pins=bit << 1)
        assert list(h.e.rx) == [(0, 0), (0x55, 0), (0xA5, 0), (0xFF, 0)]
        for _ in range(5):
            await h.step(pop=True, pins=2)
        await h.step(stop=True)
        await h.load(uart_tx(period))
        for value in (0, 0x55, 0xA5, 0xFF):
            await h.step(tx=value)
        await h.step(start=True)
        for _ in range(41 * period):
            await h.step()


@cocotb.test()
async def v2_instruction_differential(dut):
    h = Harness(dut)
    directed = """LI 1, 3
DRIVE 85, 255
IN 0, 0
IN 7, 1
MOVIS 0
RX 3
BRPIN match, 0, 1
NOP
match: BREQ next, 0, 0
next: WAITFOR 0, 1
MARK 85
WAITEVENT 1
CLEAREVENT 255
CALL subroutine
HALT
subroutine: CALL nested
RET
nested: PULLNB 1
OUT8 2
RET
"""
    await h.load(directed)
    await h.step(start=True)
    for i in range(50):
        await h.step(pins=129, events=1 if i == 15 else 0, tx=0xA5 if i == 4 else None)
    # Every extension executes under random pins, event pulses and FIFO access.
    rng = random.Random(20260924)
    choices = ["IN 2, 0", "IN 7, 1", "RX 0", "CLEARIS", "BRPIN 0, 0, 1",
               "BREQ 0, 85, 0", "BRC 0, 0", "DRIVE 85, 255", "WAITFOR 1, 1",
               "MARK 3", "WAITEVENT 3", "MOVIS 1", "OUT8 4", "PULLNB 0",
               "CLEAREVENT 3", "BRDIFF 0, 0", "WAITBR 0, 0, 1", "DELAY 2",
               "LI 1, 3", "DJNZ 0, 0", "SAMPLE 0, 255", "PULL 1", "MOVOS 0"]
    for _ in range(8):
        await h.load("LI 1, 3\n" + "\n".join(rng.choice(choices) for _ in range(40)) + "\nJMP 0")
        await h.step(start=True)
        for cycle in range(400):
            await h.step(pins=rng.randrange(256), events=rng.randrange(4),
                         tx=rng.randrange(256) if cycle % 9 == 0 else None, pop=cycle % 7 == 0)
        await h.step(enable=False)
    await h.load("DRIVE 1, 1\nRX 0\nJMP 1")
    await h.step(start=True)
    for _ in range(40):
        await h.step()
    assert h.e.errors and not h.e.running


@cocotb.test()
async def v2_fifo_boundary_differential(dut):
    h = Harness(dut)
    await h.load("PULL 0\nMOVOS 0\nOUT8 0\nJMP 0")
    for start in range(0, 15, 3):
        await h.step(tx=[start, start + 1, start + 2])
    await h.step(tx=[15, 16])  # entire pair rejected at level 15
    assert list(h.e.fifo) == list(range(15))
    await h.step(tx=15)
    await h.step(start=True)
    await h.step(tx=[16, 17, 18])  # full before the concurrent PULL
    assert h.e.regs[0] == 0 and list(h.e.fifo) == list(range(1, 16))
    for i in range(80):
        await h.step(tx=[i, i + 1, i + 2] if i % 8 == 0 else None)
    await h.step(stop=True)
    await h.step(state_reset=True)
    await h.load("RX 0\nJMP 0")
    await h.step(start=True)
    for _ in range(32):
        await h.step()
    assert len(h.e.rx) == 16
    await h.step(pop=True)  # overflow still faults against the pre-edge level
    assert len(h.e.rx) == 15 and h.e.errors and not h.e.running
