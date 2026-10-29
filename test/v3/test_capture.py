"""The RTL capture unit matches the model on every edge and stops at 32 records.

The differential test compares the status outputs and all 32 stored records
against ``model.Capture`` after every clock. The directed tests state their
expected values independently of the model.
"""

import json
import random
from pathlib import Path

import cocotb
from cocotb.triggers import Timer
from protocol_emulator.v3 import model

MODES = (0, 1, 2, 3)
DEPTH = 32
REASON_CAPACITY = 5


class Driver:
    """Steps the capture unit one clock at a time with a local shared timestamp."""

    def __init__(self, dut):
        self.dut = dut
        self.model = model.Capture()
        self.timestamp = 0
        self.steps = 0

    async def start(self):
        dut = self.dut
        dut.clk.value = 0
        dut.rst_n.value = 0
        dut.arm.value = dut.stop.value = 0
        dut.disable_capture.value = 0
        dut.trigger_mode.value = dut.trigger_mask.value = 0
        dut.now.value = dut.epoch.value = 0
        dut.pins_in.value = dut.pins_out.value = dut.pins_oe.value = dut.flags.value = 0
        dut.read_address.value = 0
        await Timer(20, "ns")

    def set_timestamp(self, value):
        """Jump the local shared timestamp; each step then advances it by one."""
        self.timestamp = value & 0xFFFFFFFF

    async def step(
        self, sample, *, arm=None, mask=255, stop=False, reset=False, disable=False
    ):
        """Apply one clock edge. ``arm`` is a trigger mode or None.

        The timestamp presented at an edge is the one stored by that edge, and
        it advances by one afterwards, as the shared timebase does.
        """
        dut = self.dut
        self.steps += 1
        stamp = self.timestamp
        dut.clk.value = 0
        dut.rst_n.value = int(not reset)
        dut.arm.value = int(arm is not None)
        dut.stop.value = int(stop)
        dut.disable_capture.value = int(disable)
        if arm is not None:
            dut.trigger_mode.value = arm
            dut.trigger_mask.value = mask
        dut.now.value = stamp & 0xFFFFFF
        dut.epoch.value = stamp >> 24
        dut.pins_in.value, dut.pins_out.value = sample[0], sample[1]
        dut.pins_oe.value, dut.flags.value = sample[2], sample[3]
        await Timer(20, "ns")
        dut.clk.value = 1
        if reset:
            self.model.reset()
        elif arm is not None:
            self.model.arm(stamp, arm, mask, *sample)
        else:
            self.model.record(stamp, *sample, stop=stop, disable=disable)
        self.timestamp = (stamp + 1) & 0xFFFFFFFF
        await Timer(1, "ns")

    async def read(self, address):
        self.dut.read_address.value = address
        await Timer(1, "ns")
        return int(self.dut.read_data.value)

    def status(self):
        dut = self.dut
        return tuple(
            int(getattr(dut, field).value)
            for field in (
                "count",
                "trigger_timestamp",
                "armed",
                "triggered",
                "truncated",
                "reason",
            )
        )

    async def compare(self, context):
        """Check status and every record against the model; raise on a mismatch."""
        expected = (
            self.model.count,
            self.model.trigger_timestamp,
            int(self.model.armed),
            int(self.model.triggered),
            int(self.model.truncated),
            self.model.reason,
        )
        actual = self.status()
        context = {
            **context,
            "step": self.steps,
            "actual": actual,
            "expected": expected,
        }

        def check(matches, detail):
            if not matches:
                path = Path("output/v3-capture-divergence.json")
                path.parent.mkdir(exist_ok=True)
                path.write_text(json.dumps({**context, **detail}, indent=2) + "\n")
            assert matches, {**context, **detail}

        check(actual == expected, {})
        for address in range(DEPTH):
            actual_record = await self.read(address)
            expected_record = self.model.read(address)
            check(
                actual_record == expected_record,
                {
                    "address": address,
                    "actual_record": actual_record,
                    "expected_record": expected_record,
                },
            )
        await Timer(7, "ns")


async def make_driver(dut):
    driver = Driver(dut)
    await driver.start()
    await driver.step((0, 0, 0, 0), reset=True)
    return driver


@cocotb.test()
async def test_v3_capture_differential(dut):
    """Directed phases then a seeded random run, across both timestamp wraps."""
    driver = await make_driver(dut)
    zero = (0, 0, 0, 0)

    async def step(sample, **kwargs):
        await driver.step(sample, **kwargs)
        await driver.compare({"sample": sample, **kwargs})

    # Fill the store untriggered-to-triggered, across the 24-bit timebase wrap.
    driver.set_timestamp(0x00FFFFF0)
    await step(zero, arm=1)
    for _ in range(40):
        await step(zero)
    await step(zero, arm=0)
    for i in range(40):
        await step((i, i, i, i & 7))
    # A held, stopped capture keeps its records and ignores later samples.
    await step(zero, arm=0)
    for i in range(1, 6):
        await step((i, 0, 0, 0))
    await step((9, 9, 9, 0), stop=True)
    for i in range(5):
        await step((i, 1, 0, 0))
    # Epoch wrap of the full 32-bit timestamp.
    driver.set_timestamp(0xFFFFFFF0)
    await step(zero, arm=0)
    for i in range(1, 40):
        await step((i & 0xFF, 0, 0, 0))

    rng = random.Random(0xCA9703)
    seed_stamps = (0x00FFFFC0, 0xFFFFFFC0, 0x12345678)
    previous = zero
    for index in range(900):
        if index % 300 == 0:
            driver.set_timestamp(seed_stamps[index // 300])
        if rng.randrange(2):
            sample = previous
        else:
            flags = rng.choice((0, 0, 0, 0x04, 0x10, 0x08, 0x01, 0x02, 0xFF, 0xF4))
            sample = (*(rng.randrange(256) for _ in range(3)), flags)
        previous = sample
        action = rng.randrange(25)
        await step(
            sample,
            arm=rng.choice(MODES) if action == 0 else None,
            mask=rng.randrange(256),
            stop=action == 1,
            reset=action == 2,
            disable=action == 3,
        )


@cocotb.test()
async def test_v3_capture_records_raw_sample_and_shared_timestamp(dut):
    """The arming record holds {epoch, now}, then inputs, outputs, enables, flags."""
    driver = await make_driver(dut)
    driver.set_timestamp(0x12345678)
    await driver.step((0xA5, 0x3C, 0x81, 0x05), arm=0)
    assert driver.status() == (1, 0x12345678, 1, 1, 0, 2)
    # Flag bit 3 is the trigger annotation: the sampled 0x05 gains 0x08.
    assert await driver.read(0) == (
        (0x12345678 << 32) | (0xA5 << 24) | (0x3C << 16) | (0x81 << 8) | 0x0D
    )
    assert await driver.read(1) == 0


@cocotb.test()
async def test_v3_capture_timestamp_carries_from_now_into_epoch(dut):
    """Consecutive records straddling the 24-bit wrap are one apart in 32 bits."""
    driver = await make_driver(dut)
    driver.set_timestamp(0x01FFFFFF)
    await driver.step((1, 0, 0, 0), arm=0)
    await driver.step((2, 0, 0, 0))
    await driver.step((3, 0, 0, 0))
    stamps = [(await driver.read(index)) >> 32 for index in range(3)]
    assert stamps == [0x01FFFFFF, 0x02000000, 0x02000001]


@cocotb.test()
async def test_v3_capture_stops_at_32_records_truncated_reason_5(dut):
    """A full store stops, reports truncated and reason 5, and keeps the first 32."""
    driver = await make_driver(dut)
    start = 0xFFFFFFF0  # the fill spans the 32-bit timestamp wrap
    driver.set_timestamp(start)
    await driver.step((0, 0, 0, 0), arm=0)
    for index in range(1, 31):
        await driver.step((index, 0, 0, 0))
        assert driver.status()[2:5] == (1, 1, 0), f"still armed after {index + 1}"
    assert driver.status()[0] == 31
    await driver.step((31, 0, 0, 0))
    count, trigger_timestamp, armed, triggered, truncated, reason = driver.status()
    assert (count, armed, triggered, truncated, reason) == (
        32,
        0,
        1,
        1,
        REASON_CAPACITY,
    )
    assert trigger_timestamp == start
    # Later samples change nothing; the records are never overwritten.
    for index in range(40):
        await driver.step((0xE0 + index % 16, 0xFF, 0xFF, 0xFF))
    assert driver.status() == (32, start, 0, 1, 1, REASON_CAPACITY)
    for index in range(DEPTH):
        stamp = (start + index) & 0xFFFFFFFF
        assert await driver.read(index) == (
            (stamp << 32) | (index << 24) | (0x08 if index == 0 else 0)
        )
    # Re-arming clears the truncated state and the old records.
    await driver.step((0, 0, 0, 0), arm=2, mask=0x03)
    assert driver.status() == (0, 0, 1, 0, 0, 1)
    assert await driver.read(0) == 0
