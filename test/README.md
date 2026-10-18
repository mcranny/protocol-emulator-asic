# Testing

Install the pinned Python dependencies in `test/requirements.txt`, Icarus
Verilog, Verilator, and Yosys. Run commands from the repository root.

## Layout and conventions

Pytest discovers `tests/v1`, `tests/v2`, and `tests/tooling`. Each module is
named `test_<behavior>.py`; test functions use `test_<behavior>` and each
parameter has an explicit descriptive ID. Modules contain a docstring,
imports, local setup fixtures, then related behavior checks.

Setup fixtures have function scope. Use `make_<object>` factory fixtures when
a test varies setup inputs. Shared fixtures live in the relevant `conftest.py`;
ordinary reusable operations live under `tests/support`. Never import another
test module. Expected values and independent decoders must remain independent
of the production implementation they check.

Cocotb scenarios live in `test/v1` and `test/v2`. Drivers, reset helpers,
harnesses, and independent decoders live in `test/support`; test modules contain
scenarios and assertions. The existing Makefile entrypoints select each suite:

| Suite | Command | Required scenarios |
| --- | --- | --- |
| Python | `python -m pytest -q` | 583 at the migration baseline |
| V1 core | `make -C test -f Makefile.core` | 3 |
| V1 pins | `make -C test` | 3 |
| V2 core | `make -C test -f Makefile.v2` | 3 |
| V2 pins | `make -C test -f Makefile.v2pins` | 14 |
| V2 capture | `make -C test -f Makefile.v2capture` | 1 |

The core suites compare architectural state against independent Python models.
Pin suites exercise external interfaces, protocol timing, independent decoding,
phase offsets, and safety boundaries. Keep schedules, random seeds, boundary
cases, and assertions when reorganizing tests.

Run the complete functional gate, including both tops' lint and synthesis,
all five induction proofs, and all six mutations, into a new directory:

```sh
python tools/validate_local.py --output build/validation/example
```

Use `--require-clean` for release evidence. Development runs on uncommitted
changes are recorded as dirty and cannot establish final release acceptance.
The [baseline migration record](../evidence/test-migration-3d5210a/README.md)
accounts for every retained case and the unchanged executable coverage.

## Gate-level simulation

The GDS workflow copies the submitted netlist into `gate_level_netlist.v` and
runs `GATES=yes make`. The default entrypoint still selects V1. V2 uses
`-f Makefile.v2pins`. Both use the CMOS5L library models under `PDK_ROOT`,
including `sg13cmos5l_udp.v`, which supplies the cell primitives. Routed timing
is a separate static-analysis gate; these simulations do not enable SDF.

For a downloaded V1 submission without copying its netlist into the source:

```sh
make -C test GATES=yes PDK_ROOT=/path/to/pdk \
  GATE_LEVEL_NETLIST=/path/to/tt_submission/tt_um_mcranny_protocol_emulator.v \
  COCOTB_RESULTS_FILE=results_gl.xml
```

Use the PDK revision recorded in the same submission's `pdk.json`. Missing
library files or unresolved primitives must fail elaboration. Never replace
cells with stubs or substitute RTL for the submitted netlist.

The complete gate retains suite XML, logs, source hashes, and tool versions in
its chosen output directory. Individual Makefile runs retain reproduction
data and divergence reports under `test/output` and their configured simulator
build directory. Preserve failed runs when rerunning a check.

## Four-state reset and state-loss audit

`v1/test_reset.py` and `v2/test_reset.py` add one audit scenario per version to
the preserved 24 ordinary scenarios. `support/reset.py` drives two uninitialized
DUT instances in `reset_tb.v`; replacement models complete state loss.
`Makefile.reset` always uses Icarus and rejects X coercion through the runner.

Run `python -m tools.validate_reset --output build/reset-new` from the repository
root. Add `--version 2 --netlist ... --pdk-root ...` for the corresponding
netlist audit. The complete functional runner executes the RTL checks and
`tools.audit_initialization`; [reset documentation](../docs/reset-and-power.md)
records assumptions, limitations, and the required restart sequence.
