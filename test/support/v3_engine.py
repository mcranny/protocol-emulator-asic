"""Cycle-exact differential harness for the V3 engine RTL.

Wraps the ``v3_engine`` DUT and a :class:`protocol_emulator.v3.model.Engine`
oracle, steps both every clock edge and compares them. The pin cells, byte and
record queues and the CRC unit are external to the DUT: the harness keeps a
second, independent copy of that environment (``self.d*``) driven only by the
DUT's output strobes, and asserts it stays identical to the model's environment
every cycle. Because the two environments stay equal, the inputs presented to
the DUT equal the inputs the model reads, so any divergence is attributable to
the engine logic.

Cycle structure (per clock, mirroring the model driver ``observe -> step ->
tick``):

1. Apply host queue pushes/pops to both environments identically.
2. Observe the external pin stimulus into both environments.
3. Drive the DUT inputs from the DUT-side environment.
4. Sample the DUT's combinational action strobes just before the rising edge.
5. Rising edge: the DUT latches its next state.
6. Step the model, apply the DUT strobes to the DUT-side environment, tick both.
7. Compare the engine registers and both environments.

On a mismatch the last 32 cycles of both sides are written to
``test/output/v3-divergence.json`` and the test fails with the cycle and field.
"""
import json
import os
from collections import deque
from collections.abc import Callable

import cocotb
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from protocol_emulator.v3 import PIN_COUNT, model
from protocol_emulator.v3.model import QUEUE_DEPTH, TIMEBASE_MASK

_OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output")
_DIVERGENCE_FILE = os.path.join(_OUTPUT_DIR, "v3-divergence.json")


def _peek_tx(queues: model.Queues) -> int:
    """The oldest TX byte without popping, or 0 when the queue is empty."""
    if queues.tx_level == 0:
        return 0
    return queues._tx[queues._tx_rd] & 0xFF


def _pin_fields(pins: list) -> dict:
    """Pack the eight pin cells into the DUT's per-pin input vectors."""
    level = value = oe = missed = polarity = free = 0
    edge_time = 0
    for i, cell in enumerate(pins):
        level |= (cell.level & 1) << i
        value |= (cell.value & 1) << i
        oe |= (cell.oe & 1) << i
        missed |= (1 if cell.missed else 0) << i
        polarity |= (cell.last_polarity & 1) << i
        free |= (1 if cell.free else 0) << i
        edge_time |= (cell.last_edge & TIMEBASE_MASK) << (24 * i)
    return {
        "level": level,
        "value": value,
        "oe": oe,
        "missed": missed,
        "polarity": polarity,
        "free": free,
        "edge_time": edge_time,
    }


def _pending(cell) -> tuple | None:
    return None if cell.pending is None else tuple(cell.pending)


class EngineHarness:
    """Drive and compare the ``v3_engine`` DUT against the model oracle."""

    def __init__(
        self,
        dut,
        image,
        owner=0xFF,
        open_drain=0,
        watches=None,
        abort_mask=0,
        now0=0,
    ):
        self.dut = dut
        self.owner = owner & 0xFF
        self.open_drain = open_drain & 0xFF
        self.abort_mask = abort_mask & 0xFF
        self.watches = list(watches or [])
        self.now0 = now0 & TIMEBASE_MASK
        self.now = self.now0

        # Model oracle and its environment.
        self.model = model.Engine(
            pins=[model.PinCell(bool((open_drain >> i) & 1)) for i in range(PIN_COUNT)],
            owner=owner,
            open_drain=open_drain,
        )
        self.model.load(image)
        self.model.watches = list(self.watches)
        self.model.abort_mask = self.abort_mask

        # DUT-side environment, driven only by the DUT output strobes.
        self.dpins = [
            model.PinCell(bool((open_drain >> i) & 1)) for i in range(PIN_COUNT)
        ]
        self.dqueues = model.Queues()
        self.dcrc = model.Crc()

        self.mem = list(image.words)
        self.prev_fetch_addr = 0
        self.history = deque(maxlen=32)
        #: CRC grant presented to the DUT and model this cycle (1 = granted).
        self._cur_grant = 1
        self._grant_fn: "Callable[[int], int] | None" = None

    # -- static configuration -------------------------------------------
    def _drive_static(self):
        d = self.dut
        d.ena.value = 1
        d.owner.value = self.owner
        d.open_drain.value = self.open_drain
        d.abort_mask.value = self.abort_mask
        for idx, sig in ((0, "watch0"), (1, "watch1")):
            if idx < len(self.watches):
                mask, value, edges = self.watches[idx]
                getattr(d, f"{sig}_en").value = 1
                getattr(d, f"{sig}_mask").value = mask & 0xFF
                getattr(d, f"{sig}_value").value = value & 0xFF
                getattr(d, f"{sig}_edges").value = edges & 0xFF
            else:
                getattr(d, f"{sig}_en").value = 0
                getattr(d, f"{sig}_mask").value = 0
                getattr(d, f"{sig}_value").value = 0
                getattr(d, f"{sig}_edges").value = 0

    def _drive_zero_dynamic(self):
        d = self.dut
        d.start.value = 0
        d.stop.value = 0
        d.program_valid.value = 1
        d.now.value = self.now
        d.fetch_data.value = 0
        d.fetch_written.value = 1
        d.pin_level.value = 0
        d.pin_drv_value.value = 0
        d.pin_drv_oe.value = 0
        d.pin_missed.value = 0
        d.pin_edge_time.value = 0
        d.pin_edge_polarity.value = 0
        d.crc_grant.value = 1
        d.slot_free.value = 0xFF
        d.tx_level.value = 0
        d.tx_data.value = 0
        d.rx_level.value = 0
        d.crc_value.value = 0

    # -- lifecycle -------------------------------------------------------
    async def reset(self):
        d = self.dut
        # Leave any read-only phase a previous run ended in before driving.
        await RisingEdge(d.clk)
        d.rst_n.value = 0
        self._drive_static()
        self._drive_zero_dynamic()
        for _ in range(3):
            await RisingEdge(d.clk)
        d.rst_n.value = 1
        await RisingEdge(d.clk)

    async def start(self):
        d = self.dut
        await FallingEdge(d.clk)
        self._drive_static()
        self._drive_zero_dynamic()
        d.start.value = 1
        d.now.value = self.now
        await ReadOnly()
        self.prev_fetch_addr = int(d.fetch_addr.value)
        await RisingEdge(d.clk)
        d.start.value = 0
        self.model.start(self.now)
        # The start cycle latches running/pc/cursor; the first instruction
        # executes on the next cycle, one timebase tick later.
        self.now = (self.now + 1) & TIMEBASE_MASK

    # -- per-cycle environment updates ----------------------------------
    def _observe_both(self, ext):
        for i in range(PIN_COUNT):
            bit = (ext >> i) & 1
            self.model.pins[i].observe(bit, self.now)
            self.dpins[i].observe(bit, self.now)

    def _drive_inputs(self):
        d = self.dut
        fields = _pin_fields(self.dpins)
        d.now.value = self.now
        d.fetch_data.value = self.mem[self.prev_fetch_addr & 0xFF]
        d.fetch_written.value = 1
        d.pin_level.value = fields["level"]
        d.pin_drv_value.value = fields["value"]
        d.pin_drv_oe.value = fields["oe"]
        d.pin_missed.value = fields["missed"]
        d.pin_edge_time.value = fields["edge_time"]
        d.pin_edge_polarity.value = fields["polarity"]
        d.crc_grant.value = self._cur_grant
        d.slot_free.value = fields["free"]
        d.tx_level.value = self.dqueues.tx_level
        d.tx_data.value = _peek_tx(self.dqueues)
        d.rx_level.value = self.dqueues.rx_level
        d.crc_value.value = self.dcrc.value

    def _read_actions(self):
        d = self.dut
        return {
            "commit": int(d.commit.value),
            "commit_value": int(d.commit_value.value),
            "commit_oe": int(d.commit_oe.value),
            "commit_due": int(d.commit_due.value),
            "tx_pull": int(d.tx_pull.value),
            "rx_push": int(d.rx_push.value),
            "rx_value": int(d.rx_value.value),
            "rx_meta": int(d.rx_meta.value),
            "crc_cfg": int(d.crc_cfg.value),
            "crc_poly": int(d.crc_poly.value),
            "crc_msb_first": int(d.crc_msb_first.value),
            "crc_load": int(d.crc_load.value),
            "crc_seed": int(d.crc_seed.value),
            "crc_bit": int(d.crc_bit.value),
            "crc_bit_value": int(d.crc_bit_value.value),
            "clear_missed": int(d.clear_missed.value),
            "fetch_addr": int(d.fetch_addr.value),
        }

    def _apply_actions(self, a):
        commit = a["commit"]
        for i in range(PIN_COUNT):
            if (commit >> i) & 1:
                self.dpins[i].schedule(
                    a["commit_due"],
                    (a["commit_value"] >> i) & 1,
                    (a["commit_oe"] >> i) & 1,
                    self.now,
                )
        if a["tx_pull"]:
            self.dqueues.pull()
        if a["rx_push"]:
            self.dqueues.push_rx(value=a["rx_value"], meta=a["rx_meta"])
        if a["crc_cfg"]:
            self.dcrc.configure(a["crc_poly"], bool(a["crc_msb_first"]))
        if a["crc_load"]:
            self.dcrc.load(a["crc_seed"])
        if a["crc_bit"]:
            self.dcrc.shift(a["crc_bit_value"])
        cm = a["clear_missed"]
        for i in range(PIN_COUNT):
            if (cm >> i) & 1:
                self.dpins[i].clear_missed()

    def _tick_both(self):
        for i in range(PIN_COUNT):
            self.model.pins[i].tick(self.now)
            self.dpins[i].tick(self.now)

    def _apply_host(self, ops):
        """Apply host queue operations to both environments identically."""
        if not ops:
            return
        for kind, payload in ops:
            if kind == "tx":
                self.model.queues.push_tx(bytes(payload))
                self.dqueues.push_tx(bytes(payload))
            elif kind == "rx_pop":
                self.model.queues.pop_rx()
                self.dqueues.pop_rx()

    # -- comparison ------------------------------------------------------
    def _dut_snapshot(self, a=None):
        d = self.dut
        snap = {
            "running": int(d.running.value),
            "halted": int(d.halted.value),
            "pc": int(d.pc.value),
            "cursor": int(d.cursor.value),
            "errors": int(d.errors.value),
            "fault_code": int(d.fault_code.value),
            "isr": int(d.dbg_isr.value),
            "events": int(d.dbg_events.value),
            "regs": [
                int(d.dbg_r0.value),
                int(d.dbg_r1.value),
                int(d.dbg_r2.value),
                int(d.dbg_r3.value),
            ],
            "pins": [
                {
                    "pending": _pending(c),
                    "value": c.value,
                    "oe": c.oe,
                    "missed": bool(c.missed),
                }
                for c in self.dpins
            ],
            "tx_level": self.dqueues.tx_level,
            "rx_level": self.dqueues.rx_level,
            "crc": self.dcrc.value,
        }
        if a is not None:
            snap["actions"] = a
        return snap

    def _model_snapshot(self):
        m = self.model
        return {
            "running": int(m.running),
            "halted": int(m.halted),
            "pc": m.pc,
            "cursor": m.cursor,
            "errors": m.errors,
            "fault_code": m.fault_code,
            "isr": m.isr,
            "events": m.events,
            "regs": list(m.regs),
            "pins": [
                {
                    "pending": _pending(c),
                    "value": c.value,
                    "oe": c.oe,
                    "missed": bool(c.missed),
                }
                for c in m.pins
            ],
            "tx_level": m.queues.tx_level,
            "rx_level": m.queues.rx_level,
            "crc": m.crc.value,
        }

    def _compare(self, cycle, dut_snap, model_snap):
        fields = [
            "running",
            "halted",
            "pc",
            "cursor",
            "errors",
            "fault_code",
            "regs",
            "tx_level",
            "rx_level",
            "crc",
        ]
        if model_snap["running"]:
            fields += ["isr", "events"]
        for field in fields:
            if dut_snap[field] != model_snap[field]:
                self._fail(cycle, field, dut_snap, model_snap)
        for i in range(PIN_COUNT):
            if dut_snap["pins"][i] != model_snap["pins"][i]:
                self._fail(cycle, f"pin{i}", dut_snap, model_snap)

    def _fail(self, cycle, field, dut_snap, model_snap):
        os.makedirs(_OUTPUT_DIR, exist_ok=True)
        dump = {
            "cycle": cycle,
            "field": field,
            "now": self.now,
            "dut": dut_snap,
            "model": model_snap,
            "history": list(self.history),
        }
        with open(_DIVERGENCE_FILE, "w") as handle:
            json.dump(dump, handle, indent=2, default=str)
        raise AssertionError(
            f"cycle {cycle}: field {field!r} diverged: "
            f"dut={dut_snap[field]!r} model={model_snap[field]!r} "
            f"(dump at {_DIVERGENCE_FILE})"
        )

    # -- main loop -------------------------------------------------------
    async def run(self, cycles, stimulus=None, host=None, crc_grant=None):
        """Run ``cycles`` clocks, comparing after every one.

        ``stimulus(cycle, now) -> int`` returns the 8-bit external pin level for
        the cycle; ``host(cycle) -> list[(kind, payload)]`` returns host queue
        operations (``("tx", bytes)`` or ``("rx_pop", None)``);
        ``crc_grant(cycle) -> int`` returns the CRC grant (default granted), so
        a test can withhold the shared CRC and confirm the engine stalls.
        """
        d = self.dut
        self._grant_fn = crc_grant
        for cycle in range(cycles):
            ext = stimulus(cycle, self.now) & 0xFF if stimulus else 0
            ops = host(cycle) if host else None
            self._cur_grant = (self._grant_fn(cycle) & 1) if self._grant_fn else 1

            await FallingEdge(d.clk)
            self._apply_host(ops)
            self._observe_both(ext)
            self._drive_inputs()
            await ReadOnly()
            actions = self._read_actions()

            await RisingEdge(d.clk)
            self.model.step(self.now, crc_grant=bool(self._cur_grant))
            self._apply_actions(actions)
            self._tick_both()

            await ReadOnly()
            dut_snap = self._dut_snapshot(actions)
            model_snap = self._model_snapshot()
            self.history.append(
                {"cycle": cycle, "now": self.now, "dut": dut_snap, "model": model_snap}
            )
            self._compare(cycle, dut_snap, model_snap)

            self.prev_fetch_addr = actions["fetch_addr"]
            self.now = (self.now + 1) & TIMEBASE_MASK

        return self

    @property
    def cycles_to_halt(self):
        return self.model.cycles_to_halt
