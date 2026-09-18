"""Cycle-by-cycle comparison against the independent Python specification."""
import json
from pathlib import Path
import random
import cocotb
from cocotb.triggers import Timer
from protocol_emulator.isa import assemble, encode
from protocol_emulator.firmware import uart_program
from protocol_emulator.model import Model


class Harness:
    def __init__(self, dut, seed=0):
        self.dut = dut
        self.model = Model()
        self.seed = seed
        self.history = []

    async def step(self, command=None, pins=0, reset=False, enable=True, frame_error=False):
        d = self.dut
        d.clk.value = 0
        d.rst_n.value = not reset
        d.ena.value = enable
        d.pins_in.value = pins
        d.frame_error.value = frame_error
        d.cmd_valid.value = command is not None
        cmd, addr, payload = command or (0, 0, 0)
        d.cmd.value = cmd
        d.addr.value = addr
        d.payload.value = payload
        await Timer(20, unit="ns")
        before_reply = int(d.reply.value)
        expected_reply = self.model.step(pins, command, frame_error, reset, enable)
        d.clk.value = 1
        await Timer(20, unit="ns")
        expected = self.model.snapshot()
        mapping = {"out": "out_value", "oe": "out_enable"}
        actual = {key: int(getattr(d, mapping.get(key, key)).value) for key in expected}
        record = dict(cycle=self.model.cycle, command=command, pins=pins,
                      expected=expected, actual=actual)
        self.history.append(record)
        try:
            assert actual == expected, record
            assert int(d.pins_oe.value) == (self.model.oe if self.model.running and enable and not reset else 0)
            if expected_reply is not None:
                wire_reply = expected_reply[0] << 32 | expected_reply[1] << 24 | expected_reply[2]
                assert before_reply == wire_reply, (hex(before_reply), expected_reply)
        except AssertionError:
            output = Path("output")
            output.mkdir(exist_ok=True)
            (output / f"divergence-{self.seed}.json").write_text(json.dumps({
                "seed": self.seed, "program": self.model.program,
                "first_divergence": record, "history": self.history[-256:],
            }, indent=2))
            raise

    async def reset(self):
        await self.step(reset=True)
        await self.step()

    async def load(self, words):
        for addr, word in enumerate(words):
            await self.step(command=(1, addr, word))
        for addr in range(len(words)):
            await self.step(command=(2, addr, 0))


@cocotb.test()
async def differential_directed(dut):
    h = Harness(dut)
    await h.reset()
    program = assemble("""LI 0, 0xA581
LI 1, 3
MOVOS 0
SET 255, 85
OE 255, 255
OUT 3, 0
OUT 7, 1
SAMPLE 0, 255
DELAY 3
WAIT 1, 1, 4
DJNZ 1, 2
PULL 0
HALT
""")
    await h.load(program)
    await h.step(command=(7, 0, 0xFF))
    await h.step(command=(3, 0, 0))
    for i in range(65):
        await h.step(pins=0xA7, command=(6, 0, 0) if i % 7 == 0 else None)
    # Invalid encoding classes and operand validation.
    for bad in (1, 0x110000, 0x210000, 0x320000, 0x400001, 0x500001,
                0x600010, 0x700100, 0x800000, 0x810000, 0x900000,
                0xA00040, 0xB00040, 0xC00001, 0xD00000, 0xE00000, 0xF00000):
        await h.step(command=(5, 0, 0))
        await h.step(command=(1, 0, bad))
    for command in ((2, 64, 0), (1, 64, 0), (10, 0, 0), (6, 1, 0),
                    (6, 0, 1), (7, 0, 256), (2, 63, 0)):
        await h.step(command=command)
    await h.reset()
    await h.load(assemble("WAIT 1, 1, 3\nHALT"))
    await h.step(command=(3, 0, 0))
    for _ in range(3):
        await h.step()
    await h.step(command=(3, 0, 0))
    await h.step()
    await h.step()
    await h.step(pins=2)  # match on timeout boundary
    await h.step()
    # Last address: legal branch vs illegal fallthrough.
    await h.reset()
    await h.step(command=(1, 0, encode("JMP", 63)))
    await h.step(command=(1, 63, encode("JMP", 0)))
    await h.step(command=(3, 0, 0))
    for _ in range(8):
        await h.step()
    await h.step(command=(4, 0, 0))
    await h.step(command=(1, 63, encode("NOP")))
    await h.step(command=(3, 0, 0))
    await h.step()
    await h.step()


@cocotb.test()
async def differential_random(dut):
    for seed in range(12):
        h = Harness(dut, seed)
        await h.reset()
        rng = random.Random(seed)
        words = []
        for _ in range(63):
            choices = [
                encode("SET", rng.randrange(256), rng.randrange(256)),
                encode("OE", rng.randrange(256), rng.randrange(256)),
                encode("LI", rng.randrange(2), rng.randrange(65536)),
                encode("PULL", rng.randrange(2)),
                encode("MOVOS", rng.randrange(2)),
                encode("OUT", rng.randrange(8), rng.randrange(2)),
                encode("SAMPLE", rng.randrange(2), rng.randrange(256)),
                encode("DELAY", rng.randrange(1, 8)),
                encode("WAIT", rng.randrange(8), rng.randrange(2), rng.randrange(1, 8)),
                encode("DJNZ", rng.randrange(2), rng.randrange(64)),
                encode("JMP", rng.randrange(64)),
                encode("NOP"),
            ]
            words.append(rng.choice(choices))
        words.append(encode("JMP", 0))
        await h.load(words)
        await h.step(command=(3, 0, 0))
        for i in range(1200):
            cmd = None
            if not h.model.running:
                cmd = (3, 0, 0)
            elif i % 17 == 0:
                cmd = (7, 0, rng.randrange(256))
            elif i % 59 == 0:
                cmd = (1, rng.randrange(64), encode("NOP"))
            elif i % 43 == 0:
                cmd = (6, 0, 0)
            await h.step(pins=rng.randrange(256), command=cmd,
                         enable=i % 701 != 700, frame_error=i % 401 == 400 and cmd is None)


@cocotb.test()
async def differential_uart(dut):
    for period in (25, 217):
        h = Harness(dut, period)
        await h.reset()
        await h.load(uart_program(period))
        for byte in (0, 0x55, 0xA5, 0xFF):
            await h.step(command=(7, 0, byte))
        await h.step(command=(7, 0, 1))  # full FIFO rejection
        await h.step(command=(3, 0, 0))
        for i in range(4 * 10 * period + 20):
            await h.step(command=(6, 0, 0) if i % 101 == 0 else None)
        await h.step(command=(7, 0, 0x5A))  # resume after starvation
        for _ in range(period):
            await h.step()
        await h.step(command=(4, 0, 0))
        await h.step(command=(3, 0, 0))
        await h.step(reset=True)
