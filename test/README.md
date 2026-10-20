# Testing

Install the pinned Python dependencies in `test/requirements.txt`. Run commands
from the repository root.

## Python suite

Pytest discovers `tests/v3` and `tests/tooling`. Each module is named
`test_<behavior>.py`; test functions use `test_<behavior>` and each parameter
has an explicit descriptive ID. Modules contain a docstring, imports, local
setup fixtures, then related behavior checks.

Setup fixtures have function scope. Use `make_<object>` factory fixtures when
a test varies setup inputs. Shared fixtures live in the relevant `conftest.py`;
ordinary reusable operations live under `tests/support`. Never import another
test module. Expected values and independent decoders must remain independent
of the production implementation they check.

Run the suite with:

```sh
python -m pytest tests -q
```

## Cocotb suites

The V1 and V2 cocotb suites and their Makefile entrypoints have been retired.
V3 cocotb suites live under `test/v3/`, each with a `test/Makefile.v3*`
entrypoint, and run in WSL with Icarus Verilog:

| Suite | Command | Scenarios |
| --- | --- | --- |
| Host SPI | `make -C test -f Makefile.v3host` | 10 |

The host SPI suite clocks frames of 47, 48 and 49 bits and a randomized
sequence, checking frames and replies against `protocol_emulator.v3.host`. It
has been run with the cocotb 1.9 install in WSL; the Makefile sets both the
1.x `MODULE` and 2.x `COCOTB_TEST_MODULES` variables.
