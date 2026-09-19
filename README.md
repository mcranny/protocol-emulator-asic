# Protocol Emulator ASIC

A programmable protocol emulator for reproducible hardware debugging on the
Tiny Tapeout IHP CMOS5L process.

The V1 implementation contains a single timing engine, 64 x 24-bit instruction
memory, a four-byte TX FIFO, and an SPI programming/control interface. UART 8N1
transmission is implemented in 25 instructions of firmware. Target system clock:
25 MHz; physical footprint: 6x4 tiles.

## Status

Python, RTL differential, pin-level UART/SPI, and formal safety checks are
implemented. Physical acceptance is tracked in [the validation record](docs/validation.md);
V1 is not released until routed timing, GDS, precheck, and gate-level tests pass.
No silicon or FPGA operation is claimed.

## Use

Install Python 3.11+, Icarus Verilog, Verilator, and Yosys. In a virtual
environment:

```sh
python -m pip install -e .
python -m pip install -r test/requirements.txt
make test
protocol-emulator assemble examples/uart_1000000.asm /tmp/uart.bin
protocol-emulator disassemble /tmp/uart.bin
protocol-emulator run /tmp/uart.bin --data '00 55 a5 ff' --cycles 1200
```

The default simulation transport starts a fresh device for each CLI invocation.
Use `run` for a complete simulated session, or the Python library for persistent
state. A Linux SPI host can install `.[hardware]` and use `--transport spi`;
that adapter is not validated on physical equipment. The CLI also exposes
`load`, `verify`, `fifo-write`, `start`, `stop`, `reset`, `status`, and `version`.

## Documentation

- [ISA, timing, host interface and pin allocation](docs/v1-contract.md)
- [Verification and clock/reset crossing review](docs/verification.md)
- [Build and physical acceptance evidence](docs/validation.md)
- [Phase 2 sequence and current throughput limits](docs/phase-2-plan.md)
- [Project datasheet](docs/info.md)

## Structure

- `src/`: synthesizable Verilog and physical configuration
- `host/protocol_emulator/`: assembler, model, loader and CLI
- `examples/`: generic pulse and UART firmware
- `tests/`: Python specification and host tests
- `test/`: core differential and pin-driven cocotb tests
- `formal/`: induction proof script
- `docs/`: interfaces, verification, and evidence

UART receive, a second engine, on-chip capture/replay, and SPI/I2C firmware are
post-V1 work.

## License

Apache License 2.0. The project foundation derives from the
[Tiny Tapeout CMOS5L Verilog template](https://github.com/TinyTapeout/ttihp-verilog-template/tree/cmos5l)
at `b86a2a781484bcab7ba522dc5de540086695a430`, retaining its license.
