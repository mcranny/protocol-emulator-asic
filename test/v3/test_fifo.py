"""The RTL queue matches model.Queues entry for entry, in both configurations.

``v3_fifo`` backs the engine's TX byte queue (8-bit entries, atomic pushes of
one to four) and its RX record queue (16-bit ``{value, meta}`` entries pushed
one at a time). Both instances are driven here from one
:class:`~protocol_emulator.v3.model.Queues`, so every level, acceptance and
head value is checked against the same oracle the engine and host models use.

The head and level are combinational from the storage, so they are read in the
settled read-only phase before each rising edge; the edge then applies the
pushes and pops, and the identical operations are applied to the model.

Cocotb 1.9.2: ``Clock`` takes a positional unit string.
"""
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import FallingEdge, ReadOnly, RisingEdge

from protocol_emulator.v3 import model
from protocol_emulator.v3.model import QUEUE_DEPTH

#: Widest TX push, matching the TXPUSH command.
TX_PUSH_MAX = 4


def _tx_pack(payload: list) -> int:
    """Pack TX bytes with the first entry in the most significant slice."""
    word = 0
    for index, byte in enumerate(payload):
        word |= (byte & 0xFF) << (8 * (TX_PUSH_MAX - 1 - index))
    return word


class QueueHarness:
    """Drives both queue instances and mirrors every step into model.Queues."""

    def __init__(self, dut) -> None:
        self.dut = dut
        self.model = model.Queues()

    async def reset(self) -> None:
        cocotb.start_soon(Clock(self.dut.clk, 20.834, "ns").start())
        self._idle()
        self.dut.rst_n.value = 0
        for _ in range(3):
            await RisingEdge(self.dut.clk)
        self.dut.rst_n.value = 1
        await RisingEdge(self.dut.clk)
        self.model = model.Queues()

    def _idle(self) -> None:
        self.dut.tx_push_count.value = 0
        self.dut.tx_push_data.value = 0
        self.dut.tx_pop.value = 0
        self.dut.rx_push_count.value = 0
        self.dut.rx_push_data.value = 0
        self.dut.rx_pop.value = 0

    def _tx_head(self) -> int | None:
        if self.model.tx_level == 0:
            return None
        return self.model._tx[self.model._tx_rd]

    def _rx_head(self) -> int | None:
        if self.model.rx_level == 0:
            return None
        value, meta = self.model._rx[self.model._rx_rd]
        return (value << 8) | meta

    async def step(
        self,
        *,
        tx_payload: list | None = None,
        tx_pop: bool = False,
        rx_record: tuple | None = None,
        rx_pop: bool = False,
    ) -> None:
        """Apply one cycle of pushes and pops, checking the combinational
        outputs against the model before the edge and the state after it."""
        tx_payload = tx_payload or []
        self.dut.tx_push_count.value = len(tx_payload)
        self.dut.tx_push_data.value = _tx_pack(tx_payload)
        self.dut.tx_pop.value = int(tx_pop)
        self.dut.rx_push_count.value = 1 if rx_record is not None else 0
        if rx_record is not None:
            value, meta = rx_record
            self.dut.rx_push_data.value = ((value & 0xFF) << 8) | (meta & 0xFF)
        else:
            self.dut.rx_push_data.value = 0
        self.dut.rx_pop.value = int(rx_pop)

        await ReadOnly()
        self._check(tx_payload, rx_record is not None)

        await RisingEdge(self.dut.clk)
        # The whole group pushes atomically; a push and a pop both apply.
        if tx_payload:
            self.model.push_tx(bytes(tx_payload))
        if tx_pop:
            self.model.pull()
        if rx_record is not None:
            self.model.push_rx(value=rx_record[0], meta=rx_record[1])
        if rx_pop:
            self.model.pop_rx()
        await FallingEdge(self.dut.clk)

    def _check(self, tx_payload: list, rx_pushing: bool) -> None:
        assert int(self.dut.tx_level.value) == self.model.tx_level, (
            f"tx level {int(self.dut.tx_level.value)} != model {self.model.tx_level}"
        )
        assert int(self.dut.rx_level.value) == self.model.rx_level, (
            f"rx level {int(self.dut.rx_level.value)} != model {self.model.rx_level}"
        )
        tx_fits = bool(tx_payload) and self.model.tx_level + len(tx_payload) <= QUEUE_DEPTH
        assert int(self.dut.tx_push_ok.value) == int(tx_fits), (
            f"tx push_ok {int(self.dut.tx_push_ok.value)} != expected {int(tx_fits)}"
        )
        rx_fits = rx_pushing and self.model.rx_level + 1 <= QUEUE_DEPTH
        assert int(self.dut.rx_push_ok.value) == int(rx_fits), (
            f"rx push_ok {int(self.dut.rx_push_ok.value)} != expected {int(rx_fits)}"
        )
        tx_head = self._tx_head()
        if tx_head is not None:
            assert int(self.dut.tx_head.value) == tx_head, (
                f"tx head {int(self.dut.tx_head.value):#x} != model {tx_head:#x}"
            )
        rx_head = self._rx_head()
        if rx_head is not None:
            assert int(self.dut.rx_head.value) == rx_head, (
                f"rx head {int(self.dut.rx_head.value):#x} != model {rx_head:#x}"
            )


@cocotb.test()
async def test_v3_fifo_reset_state(dut):
    """Reset empties both queues."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    assert int(dut.tx_level.value) == 0
    assert int(dut.rx_level.value) == 0
    assert int(dut.tx_push_ok.value) == 0
    assert int(dut.rx_push_ok.value) == 0


@cocotb.test()
async def test_v3_fifo_tx_fills_pops_and_wraps(dut):
    """A single-byte TX queue fills to depth, rejects past it, then drains in order."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    for byte in range(QUEUE_DEPTH):
        await q.step(tx_payload=[0xA0 + byte])
    # Full: a further push is rejected and nothing changes.
    await q.step(tx_payload=[0xFF])
    assert q.model.tx_level == QUEUE_DEPTH
    # Drain every byte; the heads come out in the order pushed.
    for _ in range(QUEUE_DEPTH):
        await q.step(tx_pop=True)
    assert q.model.tx_level == 0
    # Empty pop changes nothing.
    await q.step(tx_pop=True)
    assert q.model.tx_level == 0


@cocotb.test()
async def test_v3_fifo_tx_atomic_multi_push(dut):
    """A multi-byte push is all-or-nothing against the level at the cycle start."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    # Push 13 bytes in groups, leaving three slots.
    await q.step(tx_payload=[1, 2, 3, 4])
    await q.step(tx_payload=[5, 6, 7, 8])
    await q.step(tx_payload=[9, 10, 11, 12])
    await q.step(tx_payload=[13])
    assert q.model.tx_level == 13
    # A four-byte push does not fit in three slots: rejected, nothing written.
    await q.step(tx_payload=[20, 21, 22, 23])
    assert q.model.tx_level == 13
    # A three-byte push fits exactly.
    await q.step(tx_payload=[30, 31, 32])
    assert q.model.tx_level == QUEUE_DEPTH


@cocotb.test()
async def test_v3_fifo_simultaneous_push_and_pop(dut):
    """A push and a pop in the same cycle both apply."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    await q.step(tx_payload=[0x11, 0x22])
    # Pop one and push two in the same cycle.
    await q.step(tx_payload=[0x33, 0x44], tx_pop=True)
    assert q.model.tx_level == 3
    # Full queue, simultaneous push and pop: the push sees the full level and
    # is rejected, the pop still frees a slot.
    while q.model.tx_level < QUEUE_DEPTH:
        await q.step(tx_payload=[0x55])
    await q.step(tx_payload=[0x66], tx_pop=True)
    assert q.model.tx_level == QUEUE_DEPTH - 1


@cocotb.test()
async def test_v3_fifo_rx_records_round_trip(dut):
    """The 16-bit RX records fill, wrap and drain in order."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    for index in range(QUEUE_DEPTH):
        await q.step(rx_record=(0x80 + index, index))
    await q.step(rx_record=(0xEE, 0xFF))  # full: dropped
    assert q.model.rx_level == QUEUE_DEPTH
    for _ in range(QUEUE_DEPTH):
        await q.step(rx_pop=True)
    assert q.model.rx_level == 0
    await q.step(rx_pop=True)
    assert q.model.rx_level == 0


@cocotb.test()
async def test_v3_fifo_random_differential(dut):
    """Random pushes and pops on both queues agree with the model every cycle."""
    q = QueueHarness(dut)
    await q.reset()
    await FallingEdge(dut.clk)
    rng = random.Random(0x5111)
    for _ in range(3000):
        tx_count = rng.randint(0, TX_PUSH_MAX)
        tx_payload = [rng.getrandbits(8) for _ in range(tx_count)]
        rx_record = (rng.getrandbits(8), rng.getrandbits(8)) if rng.random() < 0.5 else None
        await q.step(
            tx_payload=tx_payload,
            tx_pop=rng.random() < 0.4,
            rx_record=rx_record,
            rx_pop=rng.random() < 0.4,
        )
