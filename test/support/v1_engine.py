"""Simulation helpers for v1 engine."""

import json
from pathlib import Path

from cocotb.triggers import Timer
from protocol_emulator.model import Model


class Harness:
    def __init__(self, dut, seed=0):
        self.dut = dut
        self.model = Model()
        self.seed = seed
        self.history = []

    async def step(
        self, command=None, pins=0, reset=False, enable=True, frame_error=False
    ):
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
        record = dict(
            cycle=self.model.cycle,
            command=command,
            pins=pins,
            expected=expected,
            actual=actual,
        )
        self.history.append(record)
        try:
            assert actual == expected, record
            assert int(d.pins_oe.value) == (
                self.model.oe if self.model.running and enable and not reset else 0
            )
            if expected_reply is not None:
                wire_reply = (
                    expected_reply[0] << 32
                    | expected_reply[1] << 24
                    | expected_reply[2]
                )
                assert before_reply == wire_reply, (hex(before_reply), expected_reply)
        except AssertionError:
            output = Path("output")
            output.mkdir(exist_ok=True)
            (output / f"divergence-{self.seed}.json").write_text(
                json.dumps(
                    {
                        "seed": self.seed,
                        "program": self.model.program,
                        "first_divergence": record,
                        "history": self.history[-256:],
                    },
                    indent=2,
                )
            )
            raise

    async def reset(self):
        await self.step(reset=True)
        await self.step()

    async def load(self, words):
        for addr, word in enumerate(words):
            await self.step(command=(1, addr, word))
        for addr in range(len(words)):
            await self.step(command=(2, addr, 0))
