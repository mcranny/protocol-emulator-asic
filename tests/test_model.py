import pytest
from protocol_emulator.isa import assemble
from protocol_emulator.firmware import uart_program
from protocol_emulator.model import Model, ADDRESS, BUSY, FRAME, ILLEGAL, OVERFLOW, TIMEOUT

def loaded(source):
    m = Model()
    for addr, word in enumerate(assemble(source)):
        m.step(command=(1, addr, word))
    m.step(command=(3, 0, 0))
    return m

def test_delays_and_loops():
    m = loaded("LI 0, 2\nDELAY 3\nDJNZ 0, 1\nHALT")
    pcs = []
    for _ in range(10):
        m.step()
        pcs.append(m.pc)
    assert pcs == [1, 1, 1, 2, 1, 1, 1, 2, 3, 3]
    assert not m.running

def test_wait_match_on_last_cycle():
    m = loaded("WAIT 2, 1, 3\nHALT")
    m.step()
    m.step()
    m.step(pins=4)
    assert m.pc == 1 and m.errors == 0
    m = loaded("WAIT 2, 1, 3")
    for _ in range(3):
        m.step()
    assert m.errors == TIMEOUT and not m.running

def test_fifo_boundary_and_busy():
    m = loaded("PULL 0\nPULL 1\nJMP 0")
    m.step(command=(7, 0, 0xA5))
    assert m.pc == 0 and list(m.fifo) == [0xA5]
    m.step(command=(1, 0, 0))
    assert m.regs[0] == 0xA5 and m.errors == BUSY
    m.step(command=(4, 0, 0))
    for i in range(4):
        m.step(command=(7, 0, i))
    m.step(command=(7, 0, 9))
    assert list(m.fifo) == [0, 1, 2, 3] and m.errors & OVERFLOW
    m.step(command=(5, 0, 0))
    assert not m.fifo and not m.errors and m.program[0] is not None

def test_fatal_safety_and_unwritten():
    m = loaded("SET 1, 1\nOE 1, 1")
    m.step()
    m.step()
    assert m.pins == (1, 1)
    m.step()
    assert m.errors == ILLEGAL and m.pins[1] == 0
    m = loaded("JMP 63")
    m.program[63] = 0
    m.step()
    m.step()
    assert m.errors == ADDRESS
    m = loaded("JMP 0")
    m.step(frame_error=True)
    assert m.errors == FRAME and not m.running

@pytest.mark.parametrize("period", [25, 217])
def test_uart_exact_pin_schedule(period):
    m = Model()
    m.program[:25] = uart_program(period)
    values = [0, 0x55, 0xA5, 0xFF]
    for b in values:
        m.step(command=(7, 0, b))
    m.step(command=(3, 0, 0))
    wave = []
    for _ in range(5 + 10 * period * len(values)):
        m.step()
        wave.append(m.out & 1)
    first_start = 4  # SET high, OE, PULL, MOVOS, SET low
    for frame, b in enumerate(values):
        bits = [0] + [(b >> i) & 1 for i in range(8)] + [1]
        for index, bit in enumerate(bits):
            start = first_start + (10 * frame + index) * period
            assert wave[start:start + period] == [bit] * period
    assert m.running and not m.errors
