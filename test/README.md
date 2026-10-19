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
V3 cocotb suites land under `test/v3/`.
