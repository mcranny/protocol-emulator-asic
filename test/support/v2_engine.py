"""Simulation helpers for v2 engine."""

import json
from pathlib import Path

from cocotb.triggers import Timer
from protocol_emulator.model import BUSY, FRAME, ILLEGAL
from protocol_emulator.v2.isa import assemble, encode
from protocol_emulator.v2.model import Engine


class Harness:
    def __init__(self, dut):
        self.d = dut
        self.e = Engine(capacity=64)
        self.e.owner = 255
        self.history = []

    async def step(
        self,
        pins=0,
        events=0,
        tx=None,
        pop=False,
        start=False,
        stop=False,
        reset=False,
        state_reset=False,
        enable=True,
        frame=False,
        write=None,
    ):
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
        d.rx_lookahead.value = 0
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
        expected = dict(
            pc=e.pc,
            r0=e.regs[0],
            r1=e.regs[1],
            osr=e.osr,
            isr=e.isr,
            out_value=e.out,
            out_enable=e.oe,
            events=e.events,
            marker=e.marker,
            timed_out=int(e.timeout),
            interrupted=int(e.interrupted),
            pull_empty=int(e.pull_empty),
            firmware_error=e.firmware_error,
            running=int(e.running),
            errors=e.errors,
            delay_left=e.delay_left,
            wait_left=e.wait_left,
            tx_level=len(e.fifo),
            rx_level=len(e.rx),
            link_depth=len(e.links),
            pins_out=e.pins[0],
            pins_oe=e.pins[1] if enable and not reset else 0,
        )
        actual = {key: int(getattr(d, key).value) for key in expected}
        rx_expected = (e.rx[0][0] | e.rx[0][1] << 8) if e.rx else 0
        record = dict(
            cycle=e.cycle,
            pins=pins,
            events=events,
            tx=tx,
            pop=pop,
            expected=expected,
            actual=actual,
        )
        self.history.append(record)
        if actual != expected or int(d.rx_data.value) != rx_expected:
            Path("output").mkdir(exist_ok=True)
            Path("output/v2-divergence.json").write_text(
                json.dumps(
                    {
                        "program": e.program,
                        "history": self.history[-128:],
                        "rx_expected": rx_expected,
                        "rx_actual": int(d.rx_data.value),
                    },
                    indent=2,
                )
            )
        assert actual == expected, record
        assert int(d.rx_data.value) == rx_expected
        # Independently check both read addresses against the behavioral queue,
        # including empty, one-entry, full, wraparound and concurrent push/pop.
        d.rx_lookahead.value = 1
        await Timer(1, unit="ns")
        next_expected = (e.rx[1][0] | e.rx[1][1] << 8) if len(e.rx) > 1 else 0
        assert int(d.rx_data.value) == next_expected, record
        d.rx_lookahead.value = 0
        await Timer(1, unit="ns")
        assert int(d.rx_data.value) == rx_expected, record

    async def load(self, source):
        await self.step(reset=True)
        words = assemble(source, capacity=64)
        for address, word in enumerate(words + [encode("HALT")] * (64 - len(words))):
            await self.step(write=(address, word))
