# Protocol Emulator ASIC

Programmable protocol emulator for the Jane Street Tiny Tapeout competition.
Two engines share eight protocol pins. The engines decide what happens; the pin
cells decide when.

V3 runs at 48 MHz. A shared 24-bit timebase stamps every deadline, edge and
capture record. Each pin holds one pending commit and applies it on its due
tick. Each engine has a 256-word SRAM program and a 32-bit instruction set.
The host interface is a 48-clock SPI frame.

V1 and V2 are not in this tree. What they were, and why the architecture
changed, is in [the project history](docs/history.md). The chip is described in
[the V3 architecture](docs/design/v3-architecture.md).

## Python toolchain

Python 3.11+:

```sh
python -m pip install -e .
python -m pytest tests -q
python -m mypy
```

The package is `protocol_emulator.v3`: the opcode table, assembler, image
loader, host frame codec and cycle-accurate model.

## RTL

Simulation and the induction proofs run in WSL with Icarus Verilog, cocotb and
Yosys. From the repository root:

```sh
make -C test -f Makefile.v3project
yosys -q formal/v3_integration.ys
```

Each block has its own `test/Makefile.v3*` and `formal/v3_*.ys`. See
[local builds](docs/local-build.md) and [verification](docs/verification.md).
