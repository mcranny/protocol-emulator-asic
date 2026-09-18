# Protocol Emulator ASIC

A programmable protocol emulator for reproducible hardware debugging on the
Tiny Tapeout IHP CMOS5L process.

The project will execute deterministic protocol programs, introduce controlled
faults, capture peer responses, and export experiments as replayable regression
tests. UART, SPI, and I2C are the initial protocol targets.

## Status

The repository currently contains the CMOS5L project foundation. The top-level
module provides a reset-safe pin passthrough used to validate simulation and the
physical implementation flow before the programmable engines are introduced.

## Structure

- `src/`: synthesizable Verilog and physical-flow configuration
- `test/`: cocotb testbench and simulation support
- `docs/`: project datasheet content
- `.github/workflows/`: simulation, documentation, FPGA, GDS, precheck, and gate-level jobs

## Local simulation

Install Icarus Verilog and the Python packages in `test/requirements.txt`, then
run:

```sh
make -C test
```

## License

Licensed under the Apache License 2.0. The repository foundation is derived
from the Tiny Tapeout IHP Verilog project template and retains its license.
