"""Differential and directed checks for the single-slot pin cell.

Every cycle the registered outputs of ``v3_pin_cell`` are compared against
``model.PinCell``. The bench mirrors the hardware exactly: a two-flop input
synchronizer feeds the model's ``observe`` so the timestamped edges line up, and
commits are presented only when the slot is free, reproducing the engine's
``slot_free`` interlock (an occupied slot is never overwritten).
"""

import os
import random

import cocotb
from cocotb.triggers import Timer

from protocol_emulator.v3.model import PinCell, TIMEBASE_MASK

MASK = TIMEBASE_MASK
HALF = 5  # ns, half clock period


class Bench:
    """Drives the DUT one cycle at a time and keeps a reference model in step."""

    OUTPUTS = (
        "pin_out",
        "pin_oe",
        "missed",
        "slot_free",
        "level",
        "edge_time",
        "edge_polarity",
    )

    def __init__(self, dut):
        self.dut = dut
        self.model = PinCell()
        self.now = 0
        self.sync_meta = 0
        self.sync_level = 0
        self.seed = 0

    async def reset(self, open_drain=False, start_now=0):
        dut = self.dut
        self.model = PinCell(open_drain=open_drain)
        self.sync_meta = 0
        self.sync_level = 0
        self.now = start_now & MASK
        dut.rst_n.value = 0
        dut.enable.value = 1
        dut.open_drain.value = int(open_drain)
        dut.now.value = self.now
        dut.commit.value = 0
        dut.commit_due.value = 0
        dut.commit_value.value = 0
        dut.commit_oe.value = 0
        dut.clear_missed.value = 0
        dut.pin_in.value = 0
        dut.clk.value = 0
        await Timer(HALF, units="ns")
        for _ in range(3):
            dut.clk.value = 1
            await Timer(HALF, units="ns")
            dut.clk.value = 0
            await Timer(HALF, units="ns")
        dut.rst_n.value = 1
        await Timer(HALF, units="ns")
        self._compare(self.now)

    async def step(
        self,
        *,
        pin_in=0,
        open_drain=None,
        commit=0,
        commit_due=0,
        commit_value=0,
        commit_oe=0,
        clear_missed=0,
    ):
        dut = self.dut
        now = self.now
        if open_drain is not None:
            self.model.open_drain = bool(open_drain)
        pin_in &= 1
        commit &= 1
        clear_missed &= 1
        commit_due &= MASK
        commit_value &= 1
        commit_oe &= 1

        dut.clk.value = 0
        dut.now.value = now
        dut.open_drain.value = int(self.model.open_drain)
        dut.pin_in.value = pin_in
        dut.commit.value = commit
        dut.commit_due.value = commit_due
        dut.commit_value.value = commit_value
        dut.commit_oe.value = commit_oe
        dut.clear_missed.value = clear_missed
        await Timer(HALF, units="ns")
        dut.clk.value = 1
        await Timer(HALF, units="ns")

        # Advance the model to the post-edge state for this cycle. The slot
        # fires first (like the RTL's pre-edge occupancy test); a commit is
        # only accepted when the slot was free entering the cycle.
        pre_free = self.model.free
        self.model.tick(now)
        if clear_missed:
            self.model.clear_missed()
        if commit and pre_free:
            self.model.schedule(commit_due, commit_value, commit_oe, now)
        # Two-flop synchronizer: the level the DUT presents this edge is the
        # value that was in the first flop before it.
        new_level = self.sync_meta
        self.model.observe(new_level, now)
        self.sync_level = self.sync_meta
        self.sync_meta = pin_in

        self.now = (now + 1) & MASK
        self._compare(now)

    def _compare(self, now):
        dut = self.dut
        out, oe = self.model.drive()
        expected = {
            "pin_out": out,
            "pin_oe": oe,
            "missed": int(self.model.missed),
            "slot_free": int(self.model.free),
            "level": self.model.level,
            "edge_time": self.model.last_edge,
            "edge_polarity": self.model.last_polarity,
        }
        actual = {name: int(getattr(dut, name).value) for name in self.OUTPUTS}
        if actual != expected:
            raise AssertionError(
                f"pin cell diverged at now={now} (PE_SEED={self.seed}): "
                f"expected {expected}, got {actual}"
            )


@cocotb.test()
async def test_v3_pin_cell_does_not_fire_early(dut):
    """A slot with a future deadline holds its output and stays busy."""
    b = Bench(dut)
    await b.reset(start_now=100)
    due = b.now + 5
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    while b.now < due:
        assert int(dut.slot_free.value) == 0, "slot must report busy before firing"
        assert int(dut.pin_out.value) == 0 and int(dut.pin_oe.value) == 0, (
            "output changed before the deadline"
        )
        await b.step()
    # now == due but the slot has not been clocked at the deadline yet.
    assert int(dut.slot_free.value) == 0, "slot fired before the due tick"
    assert int(dut.pin_out.value) == 0 and int(dut.pin_oe.value) == 0
    await b.step()
    assert int(dut.slot_free.value) == 1
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1


@cocotb.test()
async def test_v3_pin_cell_fires_on_the_due_tick(dut):
    """The slot fires on exactly the cycle its deadline equals now, not before."""
    b = Bench(dut)
    await b.reset(start_now=300)
    due = b.now + 3
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    while b.now < due:
        assert int(dut.pin_oe.value) == 0, "fired before the due tick"
        await b.step()
    assert b.now == due
    # Entering the due tick the slot is still busy; the next edge fires it.
    assert int(dut.slot_free.value) == 0 and int(dut.pin_oe.value) == 0
    await b.step()
    assert int(dut.slot_free.value) == 1, "slot did not fire on the due tick"
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1


@cocotb.test()
async def test_v3_pin_cell_past_due_applies_and_flags(dut):
    """A deadline already in the past applies immediately and flags missed."""
    b = Bench(dut)
    await b.reset(start_now=50)
    await b.step()
    await b.step()
    due = (b.now - 3) & MASK
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    assert int(dut.missed.value) == 1, "past-due commit did not set missed"
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1
    assert int(dut.slot_free.value) == 1, "past-due commit must not occupy the slot"


@cocotb.test()
async def test_v3_pin_cell_exactly_due_does_not_flag(dut):
    """A deadline exactly equal to now applies but never flags missed."""
    b = Bench(dut)
    await b.reset(start_now=70)
    due = b.now
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    assert int(dut.missed.value) == 0, "exactly-due commit wrongly set missed"
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1
    assert int(dut.slot_free.value) == 1


@cocotb.test()
async def test_v3_pin_cell_slot_busy_blocks_a_second_commit(dut):
    """An occupied slot reports busy and ignores a second commit."""
    b = Bench(dut)
    await b.reset(start_now=200)
    due = b.now + 4
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    assert int(dut.slot_free.value) == 0
    # Second commit (different value, immediate) while busy: must be ignored.
    await b.step(commit=1, commit_due=b.now, commit_value=0, commit_oe=1)
    assert int(dut.slot_free.value) == 0, "busy slot stopped reporting busy"
    assert int(dut.pin_out.value) == 0 and int(dut.pin_oe.value) == 0, (
        "a blocked commit still reached the output"
    )
    for _ in range(8):
        if int(dut.slot_free.value) == 1:
            break
        await b.step()
    else:
        raise AssertionError("the latched slot never fired")
    # The first commit's value (1), not the blocked one (0), must have applied.
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1


@cocotb.test()
async def test_v3_pin_cell_open_drain_never_drives_high(dut):
    """Open drain releases on a high value and drives on a low value."""
    b = Bench(dut)
    await b.reset(open_drain=True, start_now=10)
    await b.step(commit=1, commit_due=b.now, commit_value=1, commit_oe=1)
    assert int(dut.pin_out.value) == 0, "open drain drove high"
    assert int(dut.pin_oe.value) == 0, "open drain enabled the driver on a high value"
    await b.step(commit=1, commit_due=b.now, commit_value=0, commit_oe=1)
    assert int(dut.pin_out.value) == 0
    assert int(dut.pin_oe.value) == 1, "open drain did not actively drive low"


@cocotb.test()
async def test_v3_pin_cell_exposes_raw_latched_state(dut):
    """``drive_value``/``drive_oe`` expose the raw latched state, not the gated pad.

    On an open-drain pin a latched high releases the pad, so ``pin_out`` and
    ``pin_oe`` read low while the raw latched value/oe are both high. The owning
    engine needs the raw state to preserve the untouched half of a SET or OE.
    """
    b = Bench(dut)
    await b.reset(open_drain=True, start_now=20)
    # Latch value=1, oe=1: open drain releases the pad but the raw state holds.
    await b.step(commit=1, commit_due=b.now, commit_value=1, commit_oe=1)
    assert int(dut.pin_out.value) == 0 and int(dut.pin_oe.value) == 0, "pad not released"
    assert int(dut.drive_value.value) == 1, "raw latched value not exposed"
    assert int(dut.drive_oe.value) == 1, "raw latched oe not exposed"
    # Latch value=0, oe=1: the pad actively drives low and the raw state agrees.
    await b.step(commit=1, commit_due=b.now, commit_value=0, commit_oe=1)
    assert int(dut.pin_out.value) == 0 and int(dut.pin_oe.value) == 1
    assert int(dut.drive_value.value) == 0 and int(dut.drive_oe.value) == 1


@cocotb.test()
async def test_v3_pin_cell_deadline_across_the_timebase_wrap(dut):
    """A deadline on the far side of the 24-bit wrap still fires on time."""
    b = Bench(dut)
    await b.reset(start_now=(MASK - 2))
    due = (b.now + 5) & MASK  # wraps past zero
    await b.step(commit=1, commit_due=due, commit_value=1, commit_oe=1)
    while b.now != due:
        assert int(dut.slot_free.value) == 0, "slot fired early across the wrap"
        assert int(dut.pin_out.value) == 0
        await b.step()
    # now == due: this step fires the slot.
    await b.step()
    assert int(dut.slot_free.value) == 1, "slot did not fire after the wrap"
    assert int(dut.pin_out.value) == 1 and int(dut.pin_oe.value) == 1


@cocotb.test()
async def test_v3_pin_cell_random_differential(dut):
    """20,000 random cycles compared against the model every edge."""
    seed = int(os.environ.get("PE_SEED", "0"))
    rng = random.Random(seed)
    b = Bench(dut)
    b.seed = seed
    await b.reset(open_drain=bool(rng.getrandbits(1)), start_now=rng.getrandbits(24))
    try:
        for _ in range(20000):
            pin_in = rng.getrandbits(1)
            clear = 1 if rng.random() < 0.05 else 0
            open_drain = bool(rng.getrandbits(1)) if rng.random() < 0.02 else None
            commit = 0
            due = value = oe = 0
            # Honor the interlock like the engine, but sometimes present a
            # commit while busy to confirm both model and RTL ignore it.
            if b.model.free:
                commit = 1 if rng.random() < 0.4 else 0
            elif rng.random() < 0.1:
                commit = 1
            if commit:
                due = (b.now + rng.randint(-4, 12)) & MASK
                value = rng.getrandbits(1)
                oe = rng.getrandbits(1)
            await b.step(
                pin_in=pin_in,
                open_drain=open_drain,
                commit=commit,
                commit_due=due,
                commit_value=value,
                commit_oe=oe,
                clear_missed=clear,
            )
    except AssertionError:
        dut._log.error(f"differential mismatch; reproduce with PE_SEED={seed}")
        raise
