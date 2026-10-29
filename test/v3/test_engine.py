"""Cycle-exact differential of the v3_engine RTL against model.Engine.

Each test assembles a program, resets and starts the DUT, then runs it through
``support.v3_engine.EngineHarness``, which compares the RTL and the model after
every clock edge. The tests are grouped by instruction class, so a regression
points at the group that changed; the final test
runs a 100,000-cycle random program differential.
"""
import random

import cocotb
from cocotb.clock import Clock

from protocol_emulator.v3 import isa
from protocol_emulator.v3.opcodes import CONDITIONS
from support.v3_engine import EngineHarness


def _img(source):
    return isa.assemble(source)


def _illegal_image():
    image = _img("HALT\n")
    return image._replace(words=(0xFFFFFFFF,) + image.words[1:])


async def _clock(dut):
    cocotb.start_soon(Clock(dut.clk, 10, units="ns").start())


async def _drive(dut, image, cycles, **kw):
    stimulus = kw.pop("stimulus", None)
    host = kw.pop("host", None)
    crc_grant = kw.pop("crc_grant", None)
    harness = EngineHarness(dut, image, **kw)
    await harness.reset()
    await harness.start()
    await harness.run(cycles, stimulus=stimulus, host=host, crc_grant=crc_grant)
    return harness


# -- Step 2: fetch and control flow -------------------------------------
@cocotb.test()
async def test_v3_engine_control_flow(dut):
    await _clock(dut)

    # A taken branch costs exactly two cycles (JMP, bubble, HALT).
    h = await _drive(dut, _img("JMP target=2\nFAIL code=1\nHALT\n"), 8)
    assert h.model.halted and h.model.errors == 0
    assert h.cycles_to_halt == 3, "JMP + bubble + HALT"

    # An untaken branch costs one cycle.
    h = await _drive(
        dut,
        _img("BRC target=3 condition=timeout\nLI reg=0 value=7\nHALT\nFAIL code=1\n"),
        8,
    )
    assert h.model.regs[0] == 7 and h.cycles_to_halt == 3

    # CALL/RET nest twice.
    h = await _drive(
        dut,
        _img(
            "CALL target=4\nHALT\nLI reg=2 value=3\nRET\n"
            "LI reg=0 value=1\nCALL target=2\nLI reg=1 value=2\nRET\n"
        ),
        30,
    )
    assert (h.model.regs[0], h.model.regs[1], h.model.regs[2]) == (1, 2, 3)
    assert h.model.halted and h.model.errors == 0

    # A third nested CALL faults E_STACK; an empty RET faults E_STACK.
    h = await _drive(
        dut, _img("CALL target=2\nHALT\nCALL target=4\nRET\nCALL target=6\nRET\nRET\n"), 30
    )
    assert h.model.errors & 0x0004
    h = await _drive(dut, _img("RET\n"), 8)
    assert h.model.errors & 0x0004

    # DJNZ loops the expected number of times.
    h = await _drive(
        dut,
        _img("LI reg=0 value=3\nloop:\nLI reg=1 value=9\nDJNZ reg=0 target=loop\nHALT\n"),
        40,
    )
    assert h.model.regs[0] == 0 and h.model.halted

    # FAIL records a firmware fault code and halts.
    h = await _drive(dut, _img("FAIL code=42\n"), 8)
    assert h.model.errors & 0x0010 and h.model.fault_code == 42

    # An illegal word faults E_ILLEGAL.
    h = await _drive(dut, _illegal_image(), 8)
    assert h.model.errors & 0x0001

    # BREQ taken and untaken.
    h = await _drive(
        dut,
        _img("LI reg=0 value=5\nBREQ reg=0 value=5 target=skip\nFAIL code=1\nskip:\nHALT\n"),
        12,
    )
    assert h.model.halted and h.model.errors == 0


# -- Step 3: registers and ALU ------------------------------------------
@cocotb.test()
async def test_v3_engine_alu(dut):
    await _clock(dut)
    source = (
        "LI reg=0 value=0xFFFFFF\n"
        "LI reg=1 value=1\n"
        "ADD dst=0 src=1\n"                       # wrap -> carry + zero
        "BRC target=carry_ok condition=carry\n"
        "FAIL code=1\n"
        "carry_ok:\n"
        "BRC target=zero_ok condition=zero\n"
        "FAIL code=2\n"
        "zero_ok:\n"
        "LI reg=2 value=0x0000F0\n"
        "LI reg=3 value=0x00000F\n"
        "OR dst=2 src=3\n"                        # 0xFF
        "XOR dst=2 src=3\n"                        # 0xF0
        "AND dst=2 src=3\n"                        # 0x00 -> zero
        "MOV dst=1 src=0\n"
        "LI reg=0 value=0x000001\n"
        "SHL dst=0 amount=23\n"                    # 0x800000
        "SHR dst=0 amount=23\n"                    # 0x000001, carry out
        "LI reg=0 value=0x000010\n"
        "SUB dst=0 src=3\n"                        # 0x10 - 0x0F = 1
        "HALT\n"
    )
    await _drive(dut, _img(source), 40)


# -- Step 4: cursor and time --------------------------------------------
@cocotb.test()
async def test_v3_engine_time(dut):
    await _clock(dut)

    # ADVANCE never costs more than one cycle.
    h = await _drive(dut, _img("ADVANCE ticks=1000\nHALT\n"), 6)
    assert h.model.cursor == 1000 and h.cycles_to_halt == 2

    source = (
        "ADVANCE ticks=50\n"
        "LI reg=0 value=20\n"
        "ADVANCER reg=0\n"                         # cursor 70
        "CURSORGET reg=1\n"
        "NOW reg=2\n"
        "LI reg=3 value=5\n"
        "CURSORSET reg=3\n"                        # cursor 5 (behind now)
        "WAITTIME\n"                               # already passed -> no stall
        "LI reg=0 value=200\n"
        "CURSORSET reg=0\n"                        # cursor 200 (ahead) -> stall
        "WAITTIME\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 260)


# -- Step 5: commits ----------------------------------------------------
@cocotb.test()
async def test_v3_engine_commit(dut):
    await _clock(dut)

    # SET/OE/OUT/DRIVE on owned pins, including a multi-pin commit and a stall.
    source = (
        "ADVANCE ticks=100\n"
        "OUT pin=0 level=1 offset=5\n"             # schedules pin0 at 105
        "SET mask=0x0F value=0x0A offset=3\n"       # multi-pin at 103 (pin0 busy -> stall)
        "OE mask=0x0F oe=0x0F offset=0\n"
        "DRIVE value=0xFF oe=0x0F offset=2\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 220, owner=0x0F)

    # A commit to an occupied slot stalls; a second OUT waits for the first.
    source = (
        "ADVANCE ticks=500\n"
        "OUT pin=0 level=1 offset=0\n"
        "OUT pin=0 level=0 offset=1\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 40, owner=0xFF)

    # A commit to an unowned pin faults E_OWNERSHIP.
    h = await _drive(dut, _img("OUT pin=3 level=1 offset=0\nHALT\n"), 8, owner=0x01)
    assert h.model.errors & 0x0008

    # DRIVE enabling an unowned pin faults and schedules nothing.
    h = await _drive(dut, _img("DRIVE value=0xFF oe=0x10 offset=0\nHALT\n"), 8, owner=0x0F)
    assert h.model.errors & 0x0008


# -- Step 6a: inputs ----------------------------------------------------
@cocotb.test()
async def test_v3_engine_inputs(dut):
    await _clock(dut)

    def stimulus(cycle, now):
        # Pin 0 rises partway through; pins carry a steady pattern.
        return 0b1010_1100 | (0x01 if now >= 10 else 0x00)

    source = (
        "SAMPLE reg=0\n"
        "IN pin=0 msb_first=1\n"
        "IN pin=1 msb_first=1\n"
        "IN pin=2 msb_first=0\n"
        "MOVIS reg=1\n"
        "CLEARIS\n"
        "MOVIS reg=2\n"
        "EDGETIME pin=0 reg=3\n"
        "ADVANCE ticks=60\n"
        "WAITFOR pin=0 level=1\n"
        "ADVANCE ticks=60\n"
        "WAITEDGE pin=0 polarity=either\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 120, stimulus=stimulus)


# -- Step 6b: queues ----------------------------------------------------
@cocotb.test()
async def test_v3_engine_queues(dut):
    await _clock(dut)

    def host(cycle):
        if cycle == 1:
            return [("tx", bytes([0x11, 0x22, 0x33]))]
        return None

    source = (
        "PULLNB reg=0\n"                          # empty first -> pull_empty
        "BRC target=have condition=pull_empty\n"
        "FAIL code=1\n"
        "have:\n"
        "PULL reg=0\n"                            # blocks until host pushes
        "PULL reg=1\n"
        "PULL reg=2\n"
        "LI reg=3 value=0xAB\n"
        "MOVIS reg=3\n"
        "RX meta=0x5A\n"                          # push (isr, meta)
        "RX meta=0x5B\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 60, host=host)


# -- Step 6c: events ----------------------------------------------------
@cocotb.test()
async def test_v3_engine_events(dut):
    await _clock(dut)
    watches = [(0x01, 0x01, 0x01), (0x02, 0x00, 0x02)]  # pin0 rising; pin1 falling

    def stimulus(cycle, now):
        if now < 8:
            return 0x02          # pin1 high, pin0 low
        return 0x01              # pin0 rises, pin1 falls

    source = (
        "MARK code=0x7E\n"
        "ADVANCE ticks=80\n"
        "WAITEVENT mask=0x03\n"                   # wait for either watcher
        "BRC target=e0 condition=event0\n"
        "FAIL code=1\n"
        "e0:\n"
        "CLEAREVENT mask=0x03\n"
        "CLEARMISS mask=0xFF\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 120, watches=watches, abort_mask=0x03, stimulus=stimulus)


# -- Step 6d: CRC -------------------------------------------------------
@cocotb.test()
async def test_v3_engine_crc(dut):
    await _clock(dut)
    source = (
        "CRCCFG poly=0x1021 msb_first=1\n"
        "LI reg=0 value=0xFFFF\n"
        "CRCLOAD reg=0\n"
        "LI reg=1 value=1\n"
        "CRCBIT reg=1\n"
        "LI reg=1 value=0\n"
        "CRCBIT reg=1\n"
        "CRCBIT reg=1\n"
        "LI reg=1 value=1\n"
        "CRCBIT reg=1\n"
        "CRCGET reg=2\n"
        "CRCCFG poly=0xA001 msb_first=0\n"
        "CRCLOAD reg=0\n"
        "CRCBIT reg=1\n"
        "CRCGET reg=3\n"
        "HALT\n"
    )
    await _drive(dut, _img(source), 40)


@cocotb.test()
async def test_v3_engine_crc_stalls_until_granted(dut):
    """A CRC-modifying op stalls while the grant is withheld, then retires.

    The grant is dropped on every third cycle. The differential harness feeds
    the same grant to the model, which also stalls, so the engine must hold its
    PC and drive no CRC strobe on an ungranted CRC op and still reach the right
    CRC once granted. Without the stall the DUT would shift while the model did
    not and the per-cycle comparison would diverge.
    """
    await _clock(dut)
    source = (
        "CRCCFG poly=0x1021 msb_first=1\n"
        "LI reg=0 value=0xFFFF\n"
        "CRCLOAD reg=0\n"
        "LI reg=1 value=1\n"
        "CRCBIT reg=1\n"
        "CRCBIT reg=1\n"
        "CRCBIT reg=1\n"
        "CRCBIT reg=1\n"
        "CRCGET reg=2\n"
        "HALT\n"
    )
    h = await _drive(dut, _img(source), 60, crc_grant=lambda cycle: 0 if cycle % 3 == 0 else 1)
    assert h.model.halted and h.model.errors == 0
    assert h.model.cycles_to_halt > h.model.retired, "the withheld grant forced no stall"


# -- Step 7: full random differential -----------------------------------
_SAFE = [
    "NOP", "JMP", "BRC", "DJNZ", "BREQ", "LI", "MOV", "ADD", "SUB", "AND",
    "OR", "XOR", "SHL", "SHR", "ADVANCE", "CURSORGET", "NOW", "WAITTIME",
    "SET", "OE", "DRIVE", "OUT", "SAMPLE", "IN", "EDGETIME", "WAITEDGE",
    "WAITFOR", "MOVIS", "CLEARIS", "PULLNB", "RX", "MARK", "WAITEVENT",
    "CLEAREVENT", "CLEARMISS", "CRCCFG", "CRCLOAD", "CRCBIT", "CRCGET",
]


def _rand_word(rng):
    name = rng.choice(_SAFE)
    r = lambda n: rng.randrange(n)
    ops = {
        "NOP": {},
        "JMP": {"target": r(256)},
        "BRC": {"target": r(256), "condition": rng.choice(list(CONDITIONS.values()))},
        "DJNZ": {"target": r(256), "reg": r(4)},
        "BREQ": {"target": r(256), "reg": r(4), "value": r(256)},
        "LI": {"reg": r(4), "value": r(1 << 24)},
        "MOV": {"dst": r(4), "src": r(4)},
        "ADD": {"dst": r(4), "src": r(4)},
        "SUB": {"dst": r(4), "src": r(4)},
        "AND": {"dst": r(4), "src": r(4)},
        "OR": {"dst": r(4), "src": r(4)},
        "XOR": {"dst": r(4), "src": r(4)},
        "SHL": {"dst": r(4), "amount": r(32)},
        "SHR": {"dst": r(4), "amount": r(32)},
        "ADVANCE": {"ticks": r(16)},
        "CURSORGET": {"reg": r(4)},
        "NOW": {"reg": r(4)},
        "WAITTIME": {},
        "SET": {"mask": r(256), "value": r(256), "offset": r(8)},
        "OE": {"mask": r(256), "oe": r(256), "offset": r(8)},
        "DRIVE": {"value": r(256), "oe": r(256), "offset": r(8)},
        "OUT": {"pin": r(8), "level": r(2), "offset": r(8)},
        "SAMPLE": {"reg": r(4)},
        "IN": {"pin": r(8), "msb_first": r(2)},
        "EDGETIME": {"pin": r(8), "reg": r(4)},
        "WAITEDGE": {"pin": r(8), "polarity": rng.choice([0, 1, 2])},
        "WAITFOR": {"pin": r(8), "level": r(2)},
        "MOVIS": {"reg": r(4)},
        "CLEARIS": {},
        "PULLNB": {"reg": r(4)},
        "RX": {"meta": r(256)},
        "MARK": {"code": r(256)},
        "WAITEVENT": {"mask": r(256)},
        "CLEAREVENT": {"mask": r(256)},
        "CLEARMISS": {"mask": r(256)},
        "CRCCFG": {"poly": r(1 << 16), "msb_first": r(2)},
        "CRCLOAD": {"reg": r(4)},
        "CRCBIT": {"reg": r(4)},
        "CRCGET": {"reg": r(4)},
    }[name]
    return isa.encode(name, **ops)


@cocotb.test()
async def test_v3_engine_random(dut):
    await _clock(dut)
    rng = random.Random(0xC0FFEE)
    words = tuple(_rand_word(rng) for _ in range(256))
    image = isa.Image(words=words, labels={}, used=256)

    watches = [(0b0000_0011, 0b0000_0001, 0b0000_0011),
               (0b0000_1100, 0b0000_1000, 0b0000_1100)]

    def stimulus(cycle, now):
        return rng.randrange(256)

    def host(cycle):
        ops = [("rx_pop", None)]
        if rng.random() < 0.1:
            ops.append(("tx", bytes(rng.randrange(256) for _ in range(rng.randint(1, 3)))))
        return ops

    await _drive(
        dut,
        image,
        100_000,
        owner=0xFF,
        open_drain=0x30,
        watches=watches,
        abort_mask=0x03,
        now0=0xFFFFE0,
        stimulus=stimulus,
        host=host,
    )
