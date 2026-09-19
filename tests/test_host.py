import pytest
from protocol_emulator.isa import assemble
from protocol_emulator.host import Device, DeviceError, SimulationTransport
from protocol_emulator.cli import main

def test_host_load_readback_and_fifo():
    t = SimulationTransport(cycles_per_frame=1)
    d = Device(t)
    words = assemble("PULL 0\nJMP 0")
    d.load(words)
    d.fifo_write(bytes([0, 0x55, 0xA5, 0xFF]))
    assert d.request(8) == 4
    with pytest.raises(DeviceError):
        d.fifo_write(bytes([1]))
    d.start()
    with pytest.raises(DeviceError):
        d.load(words)
    d.stop()
    d.reset()
    assert d.status() == 6
    d.verify(words)

def test_version_and_response_errors():
    d = Device(SimulationTransport(cycles_per_frame=1))
    d.check_version()
    with pytest.raises(DeviceError):
        d.request(2, 64)
    with pytest.raises(ValueError):
        d.load([0xF00000])
    assert d.transport.model.program == [None] * 64

def test_cli_roundtrip_and_run(tmp_path, capsys):
    source = tmp_path / "test.asm"
    image = tmp_path / "test.bin"
    source.write_text("NOP\nHALT\n")
    assert main(["assemble", str(source), str(image)]) == 0
    assert main(["disassemble", str(image)]) == 0
    assert capsys.readouterr().out == "NOP\nHALT\n"
    assert main(["run", str(image), "--cycles", "10"]) == 0
    assert '"running": 0' in capsys.readouterr().out

def test_cli_invalid_image(tmp_path):
    image = tmp_path / "bad.bin"
    image.write_bytes(b"x")
    with pytest.raises(SystemExit) as err:
        main(["load", str(image)])
    assert err.value.code == 2

def test_shorter_load_clears_old_tail():
    t = SimulationTransport(cycles_per_frame=1)
    d = Device(t)
    d.load(assemble("NOP\nNOP\nJMP 0"))
    d.load(assemble("NOP"))
    assert t.model.program[1:] == [0xC00000] * 63
    d.start()
    for _ in range(3):
        t.model.step()
    assert not t.model.running and t.model.errors == 0
