"""Compare capture snapshots and all stored records at every sampled edge."""
import json
from pathlib import Path
import random

import cocotb
from cocotb.triggers import Timer

from protocol_emulator.v2.capture import Capture, Sample


@cocotb.test()
async def v2_capture_differential(dut):
    model = Capture(timestamp_bits=8)
    modes = ("immediate", "pin", "marker", "error")
    reasons = ("reset", "armed", "capturing", "stopped", "untriggered", "capacity", "timestamp")
    dut.clk.value = 0
    dut.rst_n.value = 0
    dut.arm.value = dut.stop.value = 0
    dut.trigger_mode.value = dut.trigger_mask.value = 0
    dut.pins_in.value = dut.pins_out.value = dut.pins_oe.value = dut.flags.value = 0
    dut.read_address.value = 0
    await Timer(20, unit="ns")
    steps = 0

    async def step(sample, *, arm=None, mask=255, stop=False, reset=False):
        nonlocal steps
        steps += 1
        dut.clk.value = 0
        dut.rst_n.value = not reset
        dut.arm.value = arm is not None
        dut.stop.value = stop
        if arm is not None:
            dut.trigger_mode.value = modes.index(arm)
            dut.trigger_mask.value = mask
        dut.pins_in.value = sample.inputs
        dut.pins_out.value = sample.outputs
        dut.pins_oe.value = sample.enables
        dut.flags.value = sample.flags
        await Timer(20, unit="ns")
        dut.clk.value = 1
        if reset:
            model.reset()
        elif arm is not None:
            model.arm(sample, arm, mask)
        elif stop:
            model.stop()
        else:
            model.tick(sample)
        await Timer(1, unit="ns")
        actual = tuple(int(getattr(dut, field).value) for field in (
            "count", "timestamp", "trigger_timestamp", "armed", "triggered", "truncated", "reason"))
        expected = (len(model.records), model.cycle, model.trigger_cycle or 0,
                    model.armed, model.triggered, model.truncated, reasons.index(model.reason))
        context = {"seed": 0xCA9702, "step": steps, "sample": sample.__dict__,
                   "arm": arm, "mask": mask, "stop": stop, "reset": reset,
                   "actual": actual, "expected": expected}
        def check(matches, detail):
            if not matches:
                path = Path("output/v2-capture-divergence.json")
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps({**context, **detail}, indent=2) + "\n")
            assert matches, {**context, **detail}
        check(actual == expected, {})
        for address in range(32):
            dut.read_address.value = address
            await Timer(1, unit="ns")
            actual_record = int(dut.read_data.value)
            expected_record = model.records[address].packed if address < len(model.records) else 0
            check(actual_record == expected_record, {"address": address, "actual_record": actual_record,
                                                     "expected_record": expected_record})
        await Timer(7, unit="ns")

    zero = Sample(0, 0, 0)
    await step(zero, reset=True)
    # Untriggered exhaustion, triggered exhaustion, full buffer and held freeze.
    for mode in ("pin", "immediate"):
        await step(zero, arm=mode)
        for _ in range(260):
            await step(zero)
    await step(zero, arm="immediate")
    for i in range(40):
        await step(Sample(i, i, i, i & 7))
    rng = random.Random(0xCA9702)
    for _ in range(600):
        sample = Sample(*(rng.randrange(256) for _ in range(3)), rng.randrange(8))
        action = rng.randrange(25)
        await step(sample, arm=rng.choice(modes) if action == 0 else None,
                   mask=rng.randrange(256), stop=action == 1, reset=action == 2)
