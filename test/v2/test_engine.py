"""V2 candidate core differential checks; no physical acceptance claim."""

import random

import cocotb
from protocol_emulator.v2.firmware import uart_rx, uart_tx
from support.v2_engine import Harness


@cocotb.test()
async def test_v2_uart_differential(dut):
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
async def test_v2_instruction_differential(dut):
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
    choices = [
        "IN 2, 0",
        "IN 7, 1",
        "RX 0",
        "CLEARIS",
        "BRPIN 0, 0, 1",
        "BREQ 0, 85, 0",
        "BRC 0, 0",
        "DRIVE 85, 255",
        "WAITFOR 1, 1",
        "MARK 3",
        "WAITEVENT 3",
        "MOVIS 1",
        "OUT8 4",
        "PULLNB 0",
        "CLEAREVENT 3",
        "BRDIFF 0, 0",
        "WAITBR 0, 0, 1",
        "DELAY 2",
        "LI 1, 3",
        "DJNZ 0, 0",
        "SAMPLE 0, 255",
        "PULL 1",
        "MOVOS 0",
    ]
    for _ in range(8):
        await h.load(
            "LI 1, 3\n" + "\n".join(rng.choice(choices) for _ in range(40)) + "\nJMP 0"
        )
        await h.step(start=True)
        for cycle in range(400):
            await h.step(
                pins=rng.randrange(256),
                events=rng.randrange(4),
                tx=rng.randrange(256) if cycle % 9 == 0 else None,
                pop=cycle % 7 == 0,
            )
        await h.step(enable=False)
    await h.load("DRIVE 1, 1\nRX 0\nJMP 1")
    await h.step(start=True)
    for _ in range(40):
        await h.step()
    assert h.e.errors and not h.e.running


@cocotb.test()
async def test_v2_fifo_boundary_differential(dut):
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
