"""Queue instructions, the BRC queue conditions, and the shared CRC unit."""
from protocol_emulator.v3 import isa, model, opcodes


def new_engine(source: str, owner: int = 0xFF, crc: "model.Crc | None" = None) -> model.Engine:
    """Assemble ``source`` onto a fresh engine owning ``owner``'s pins."""
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=owner, crc=crc)
    engine.load(isa.assemble(source))
    return engine


def run_to_halt(engine: model.Engine, max_cycles: int = 50) -> None:
    """Step ``engine`` (with no input) until it halts or ``max_cycles`` elapse."""
    for now in range(max_cycles):
        engine.step(now)
        for cell in engine.pins:
            cell.tick(now)
        if engine.halted:
            return


def crc_direct(data: bytes, poly: int, seed: int, msb_first: bool) -> int:
    """Reference CRC over ``data`` using the model's :class:`~model.Crc` directly."""
    unit = model.Crc()
    unit.configure(poly=poly, msb_first=msb_first)
    unit.load(seed)
    for byte in data:
        order = range(7, -1, -1) if msb_first else range(8)
        for index in order:
            unit.shift((byte >> index) & 1)
    return unit.value


def crc_program(data: bytes, poly: int, seed: int, msb_first: bool) -> str:
    """Assembly that drives ``data`` through the CRC opcodes one bit at a time."""
    lines = [
        f"CRCCFG poly={poly} msb_first={1 if msb_first else 0}",
        f"LI reg=0 value={seed}",
        "CRCLOAD reg=0",
    ]
    for byte in data:
        order = range(7, -1, -1) if msb_first else range(8)
        for index in order:
            lines.append(f"LI reg=1 value={(byte >> index) & 1}")
            lines.append("CRCBIT reg=1")
    lines.append("CRCGET reg=2")
    lines.append("HALT")
    return "\n".join(lines) + "\n"


# -- PULL / PULLNB / RX -------------------------------------------------


def test_pull_stalls_on_an_empty_tx_queue_then_pops() -> None:
    engine = new_engine("PULL reg=0\nHALT\n")
    engine.start(0)
    engine.step(0)
    stalled, halted = engine.stalled, engine.halted
    assert stalled and not halted
    assert engine.regs[0] == 0
    engine.queues.push_tx(b"\x7e")
    engine.step(1)
    assert not engine.stalled
    assert engine.regs[0] == 0x7E
    engine.step(2)
    assert engine.halted


def test_pull_preserves_fifo_order() -> None:
    engine = new_engine("PULL reg=0\nPULL reg=1\nPULL reg=2\nHALT\n")
    engine.start(0)
    engine.queues.push_tx(b"\x11\x22\x33")
    for now in range(4):
        engine.step(now)
    assert (engine.regs[0], engine.regs[1], engine.regs[2]) == (0x11, 0x22, 0x33)


def test_pullnb_on_empty_sets_pull_empty_and_leaves_the_register() -> None:
    engine = new_engine("PULLNB reg=0\nHALT\n")
    engine.start(0)
    engine.regs[0] = 0xABC
    engine.step(0)
    assert engine.pull_empty is True
    assert engine.regs[0] == 0xABC


def test_pullnb_on_nonempty_pops_and_clears_pull_empty() -> None:
    engine = new_engine("PULLNB reg=0\nHALT\n")
    engine.start(0)
    engine.pull_empty = True
    engine.queues.push_tx(b"\x5a")
    engine.step(0)
    assert engine.pull_empty is False
    assert engine.regs[0] == 0x5A


def test_rx_pushes_the_isr_and_meta() -> None:
    engine = new_engine("RX meta=0xC3\nHALT\n")
    engine.start(0)
    engine.isr = 0x9A
    engine.step(0)
    assert engine.queues.rx_level == 1
    assert engine.queues.pop_rx() == (0x9A, 0xC3)


def test_rx_to_a_full_queue_faults_without_retiring() -> None:
    engine = new_engine("RX meta=0x01\nHALT\n")
    engine.start(0)
    for _ in range(16):
        engine.queues.push_rx(0, 0)
    engine.step(0)
    assert engine.errors & model.E_QUEUE
    assert engine.halted
    assert engine.retired == 0
    assert engine.queues.rx_level == 16, "nothing is pushed on overflow"


# -- BRC queue conditions ------------------------------------------------


def test_brc_tx_empty_branches_on_the_boundary() -> None:
    source = "BRC target=ok condition=tx_empty\nFAIL code=1\nok:\nHALT\n"
    empty = new_engine(source)
    empty.start(0)
    run_to_halt(empty)
    assert empty.halted and empty.errors == 0

    filled = new_engine(source)
    filled.start(0)
    filled.queues.push_tx(b"\x01")
    run_to_halt(filled)
    assert filled.errors & model.E_FIRMWARE


def test_brc_rx_full_branches_on_the_boundary() -> None:
    source = "BRC target=ok condition=rx_full\nFAIL code=1\nok:\nHALT\n"
    full = new_engine(source)
    full.start(0)
    for _ in range(16):
        full.queues.push_rx(0, 0)
    run_to_halt(full)
    assert full.halted and full.errors == 0

    almost = new_engine(source)
    almost.start(0)
    for _ in range(15):
        almost.queues.push_rx(0, 0)
    run_to_halt(almost)
    assert almost.errors & model.E_FIRMWARE


def test_brc_pull_empty_reflects_the_last_pullnb() -> None:
    source = "PULLNB reg=0\nBRC target=ok condition=pull_empty\nFAIL code=1\nok:\nHALT\n"
    empty = new_engine(source)
    empty.start(0)
    run_to_halt(empty)
    assert empty.halted and empty.errors == 0

    filled = new_engine(source)
    filled.start(0)
    filled.queues.push_tx(b"\x01")
    run_to_halt(filled)
    assert filled.errors & model.E_FIRMWARE, "a popped byte clears pull_empty"


# -- CRC through the opcodes --------------------------------------------


def test_crc16_ccitt_through_the_opcodes() -> None:
    engine = new_engine(crc_program(b"123456789", 0x1021, 0xFFFF, True))
    engine.start(0)
    run_to_halt(engine, max_cycles=400)
    assert engine.halted
    assert engine.regs[2] == 0x29B1


def test_crc16_arc_through_the_opcodes() -> None:
    engine = new_engine(crc_program(b"123456789", 0xA001, 0x0000, False))
    engine.start(0)
    run_to_halt(engine, max_cycles=400)
    assert engine.halted
    assert engine.regs[2] == 0xBB3D


def test_crccfg_through_the_opcode_does_not_disturb_the_state() -> None:
    source = (
        "CRCCFG poly=0x1021 msb_first=1\n"
        "LI reg=0 value=0xFFFF\n"
        "CRCLOAD reg=0\n"
        "LI reg=1 value=1\n"
        "CRCBIT reg=1\n"
        "CRCGET reg=2\n"
        "CRCCFG poly=0x1021 msb_first=1\n"
        "CRCGET reg=3\n"
        "HALT\n"
    )
    engine = new_engine(source)
    engine.start(0)
    run_to_halt(engine)
    assert engine.regs[2] == engine.regs[3]


# -- shared CRC across two engines (G11) --------------------------------


def test_two_engines_chain_the_shared_crc_in_a_fixed_order() -> None:
    crc = model.Crc()
    pins = [model.PinCell() for _ in range(8)]
    e0 = model.Engine(pins=pins, owner=0x0F, crc=crc)
    e1 = model.Engine(pins=pins, owner=0xF0, crc=crc)
    e0.load(isa.assemble("CRCBIT reg=0\nHALT\n"))
    e1.load(isa.assemble("CRCBIT reg=0\nHALT\n"))
    crc.configure(0x1021, True)
    crc.load(0x0000)
    e0.start(0)
    e1.start(0)
    e0.regs[0] = 1  # engine 0 shifts a 1
    e1.regs[0] = 0  # engine 1 shifts a 0
    e0.step(0)  # applied first
    e1.step(0)  # chained on engine 0's result in the same cycle

    reference = model.Crc()
    reference.configure(0x1021, True)
    reference.load(0x0000)
    reference.shift(1)
    reference.shift(0)
    assert crc.value == reference.value


def test_two_engines_use_the_shared_crc_in_disjoint_bursts() -> None:
    crc = model.Crc()
    pins = [model.PinCell() for _ in range(8)]
    e0 = model.Engine(pins=pins, owner=0x0F, crc=crc)
    e1 = model.Engine(pins=pins, owner=0xF0, crc=crc)
    e0.load(isa.assemble(crc_program(b"\xab", 0x1021, 0xFFFF, True)))
    e1.load(isa.assemble(crc_program(b"\xcd", 0xA001, 0x0000, False)))
    e0.start(0)
    run_to_halt(e0, max_cycles=100)
    e1.start(0)
    run_to_halt(e1, max_cycles=100)
    assert e0.regs[2] == crc_direct(b"\xab", 0x1021, 0xFFFF, True)
    assert e1.regs[2] == crc_direct(b"\xcd", 0xA001, 0x0000, False)


# -- timing-class audit --------------------------------------------------


def test_every_opcode_has_a_model_handler() -> None:
    engine = new_engine("HALT\n")
    for name in opcodes.OPS:
        assert name in engine._handlers, name


def test_single_timing_ops_each_retire_in_one_cycle() -> None:
    source = (
        "SAMPLE reg=0\n"
        "IN pin=0 msb_first=1\n"
        "EDGETIME pin=0 reg=0\n"
        "MOVIS reg=0\n"
        "CLEARIS\n"
        "PULLNB reg=0\n"
        "RX meta=0\n"
        "MARK code=1\n"
        "CLEAREVENT mask=0xFF\n"
        "CLEARMISS mask=0xFF\n"
        "CRCCFG poly=0x1021 msb_first=1\n"
        "CRCLOAD reg=0\n"
        "CRCBIT reg=0\n"
        "CRCGET reg=0\n"
        "HALT\n"
    )
    engine = new_engine(source)
    engine.start(0)
    run_to_halt(engine, max_cycles=40)
    assert engine.halted and engine.errors == 0
    assert engine.retired == 15, "fourteen single ops plus HALT, one per cycle"
    assert engine.cycles_to_halt == 15, "no stalls and no branch bubbles"
