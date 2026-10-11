# Protocol Emulator ASIC

Programmable protocol emulator ASIC with dual timing engines, SPI host control,
and triggered capture for reproducible hardware debugging.

V2 runs two independent 25 MHz engines, each with 64 24-bit instruction words,
16-byte TX and 16-record RX queues. Firmware implements UART TX/RX, SPI modes
0–3 as controller or target, and I2C 100/400 kHz as controller or target. A
32-record triggered capture unit records pin activity without driving protocols.

The V2 release candidate is under preparation. [Stock Tiny Tapeout qualification](docs/v2-tt-flow.md)
and fresh same-revision acceptance remain required before release. Historical
local closure applies only to its recorded source. No silicon or FPGA operation
is claimed. The physical target remains 6×4 tiles, 40 ns clock, 1.5 ns maximum
transition and 0.25 ns uncertainty; the anticipated 8×4 template is deferred.

## Try the V2 examples

Use Python 3.11+ and a virtual environment:

```sh
python -m pip install -e .
python examples/v2/run.py --output build/examples-new
```

This generates assembly and binary images and checks 14 protocol scenarios,
including UART duplex at both rates, all SPI modes/roles, and both I2C rates/roles.
See [pins, expected traces and limits](docs/example-programs.md). The examples
exercise the cycle model; RTL pin tests separately exercise the SPI host interface.
The existing `protocol-emulator` CLI remains V1. V2 assembly, decoding and loading
use the [Python toolchain](docs/assembler-toolchain.md).

## Verify and build documentation

Install Icarus Verilog, Verilator and Yosys, then:

```sh
python -m pip install -r test/requirements.txt
python tools/validate_local.py --require-clean --output build/validation-new
```

For development, omit `--require-clean`; the report records dirty-source evidence.
The full gate covers both revisions, five induction proofs, reset/initialization
checks and six mutations. Physical execution requires an exact candidate SHA
and the `v2-physical` environment approval. See [verification](docs/verification.md)
and [release evidence](docs/release-evidence.md) for scope and remaining gates.

Build HTML in a separate documentation environment:

```sh
python -m venv .venv-docs
.venv-docs/bin/python -m pip install -r requirements-docs.txt
.venv-docs/bin/python -m sphinx -n -W --keep-going -b html docs build/docs-html
```

CI retains HTML as an artifact; Pages deployment is deferred.
[The submission index](docs/submission.md) links architecture, ISA, toolchain,
examples, verification, physical evidence and the required Markdown datasheet.
[Track 2](docs/verification-track-2.md) records expanded RTL properties and Lean
semantics work after the V2 candidate baseline is frozen.

## Repository

`src/` contains RTL; `host/protocol_emulator/` holds the assembler, models and
host API; `examples/v2/` provides executable protocol examples. Python tests are
under `tests/v1`, `tests/v2` and `tests/tooling`; cocotb scenarios are under
`test/v1` and `test/v2` with shared support. `formal/` contains proof scripts and
assumptions; `docs/` is the Sphinx source tree. V1 remains covered by explicit
regression targets and [compatibility documentation](docs/v1-compatibility.md).

## License

Apache License 2.0. The project foundation derives from the
[Tiny Tapeout CMOS5L Verilog template](https://github.com/TinyTapeout/ttihp-verilog-template/tree/cmos5l)
at `b86a2a781484bcab7ba522dc5de540086695a430`, retaining its license.
