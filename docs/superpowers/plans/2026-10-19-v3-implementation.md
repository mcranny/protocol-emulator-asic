---
orphan: true
---

# Protocol Emulator ASIC V3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development
> (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use
> checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the V3 protocol emulator ASIC — two programmable engines, SRAM program memory,
deadline-scheduled pin cells on a shared 48 MHz timebase — and take it through a locally hardened,
precheck-clean GDS for the Jane Street competition due 18 January 2027.

**Architecture:** Engines decide *what* happens; pin cells decide *when*. A free-running 24-bit
counter at 48 MHz is the single time reference. Each engine owns a private cursor and schedules pin
changes at `cursor + offset`; each of the eight protocol pins holds one pending
`{due_time, value, oe}` slot and applies it when the signed 24-bit distance to `now` reaches zero.
Programs live in one 256x32 SRAM macro per engine with a registered (one-cycle-late) read.

**Tech Stack:** Verilog-2005 RTL; Python 3.11 (assembler, cycle-accurate model, protocol peers,
host API); cocotb + Icarus Verilog for simulation; Yosys/SymbiYosys for formal; LibreLane via the
Tiny Tapeout flow on IHP SG13G2 CMOS5L; Vivado for the Arty A7-35T prototype.

**Source spec:** `docs/superpowers/specs/2026-10-19-v3-design.md`. Read it before Task 1. Where this
plan and the spec disagree, the spec wins and the plan gets fixed.

## Global Constraints

- **Clock:** 48 MHz. `CLOCK_PERIOD` is `20.833` ns. Every timing constant derives from this.
- **Timebase:** 24-bit, free-running, wraps every 349.5 ms. An 8-bit epoch extends it to 32 bits
  for capture and host readout.
- **Instruction word:** 32 bits. Opcode in `[31:26]`, operand in `[25:0]`. Reserved operand bits
  must be zero or the word is illegal.
- **Program memory:** exactly 256 words per engine. A program that does not fit fails assembly.
- **Registers:** `R0`-`R3`, 24 bits each.
- **Pin commit offset:** 10 bits, 0-1023 ticks.
- **Scheduling window:** signed 24-bit distance, ±8,388,608 ticks (±174.8 ms).
- **Pins:** `ui[0]` SCK, `ui[1]` CS_n, `ui[2]` MOSI; `uo[0]` MISO, `uo[1]` engine 0 running,
  `uo[2]` engine 1 running, `uo[3]` aggregate sticky error; `uio[7:0]` protocol pins.
- **Host frame:** exactly 48 SPI clocks, mode 0, MSB first: `cmd[7:0]`, `addr[7:0]`,
  `payload[31:0]`. Any other length is rejected.
- **Top module:** `tt_um_mcranny_protocol_emulator_v3`.
- **Verilog dialect:** Verilog-2005 only, matching existing `src/*.v`. No SystemVerilog. Four-space
  indentation, `default_nettype none` at the top of every file, `default_nettype wire` at the end.
- **Python:** 3.11, standard library only for the core package (`pyproject.toml` lists no runtime
  dependencies and that stays true). Type hints on all public functions; `mypy --strict` clean.
- **Git identity:** every commit must be authored `Matthew Cranny <crannymatthew@gmail.com>`.
  No `Co-authored-by` trailers, no agent attribution, in commit messages, PR bodies, or code
  comments. A local `commit-msg` hook in `.git/hooks/` strips these; do not remove it. Verify with
  `git log -1 --format='%an <%ae>%n%B'` after each commit.
- **No pushing.** Commit locally only. Publishing to GitHub is a separate, explicitly requested
  action.
- **Working directory:** `C:\Users\matthewc\Documents\Projects\protocol-emulator-asic` on Windows,
  or `/mnt/c/Users/matthewc/Documents/Projects/protocol-emulator-asic` from WSL. All build output
  goes under `build/`, which is gitignored.
- **Naming:** V3 RTL files are `src/v3_*.v`. V3 Python lives in `host/protocol_emulator/v3/`.
  V3 pure-Python tests live in `tests/v3/`. V3 cocotb tests live in `test/v3/` driven by
  `test/Makefile.v3*`. V3 formal scripts are `formal/v3_*.ys`.

---

## File Structure

Created in this plan:

| Path | Responsibility |
|---|---|
| `host/protocol_emulator/v3/opcodes.py` | The single opcode table. Encoding, operand fields, timing class. Everything else is generated from or checked against it. |
| `host/protocol_emulator/v3/isa.py` | Encode, decode, assemble. Label resolution, 256-word image emission. |
| `host/protocol_emulator/v3/timing.py` | Seconds-to-cycles conversion at 48 MHz and the named protocol constants. |
| `host/protocol_emulator/v3/model.py` | Cycle-accurate model: timebase, pin cells, engines, queues, CRC, capture. |
| `host/protocol_emulator/v3/host.py` | 48-bit host frame codec and the command map. |
| `host/protocol_emulator/v3/loader.py` | Image load, readback, configure, START sequencing. |
| `host/protocol_emulator/v3/peers.py` | Independent wire-level protocol decoders used as test oracles. |
| `host/protocol_emulator/v3/firmware/__init__.py` | Firmware program registry. |
| `host/protocol_emulator/v3/firmware/uart.py` | UART TX and RX programs. |
| `host/protocol_emulator/v3/firmware/spi.py` | SPI controller and target, modes 0-3. |
| `host/protocol_emulator/v3/firmware/i2c.py` | I2C controller, target, bus clear. |
| `host/protocol_emulator/v3/firmware/ws2812.py` | WS2812 transmit — the "not designed for" demo. |
| `src/v3_timebase.v` | 24-bit counter plus 8-bit epoch. |
| `src/v3_pin_cell.v` | One pin: pending slot, deadline compare, open-drain, edge timestamp. |
| `src/v3_input_filter.v` | Three-sample qualifier, ported from `src/v2_input_filter.v`. |
| `src/v3_progmem.v` | SRAM wrapper: registered read, validity bitmap, `A_MEN` gating, blackbox/behavioral switch. |
| `src/v3_crc.v` | Shared 16-bit CRC/LFSR with configurable polynomial and direction. |
| `src/v3_engine.v` | Fetch pipeline, decode, execute, cursor, queues, stalls. |
| `src/v3_host_spi.v` | 48-clock frame receiver and response shifter. |
| `src/v3_capture.v` | 32-record capture on the shared 32-bit timestamp. |
| `src/v3_project.v` | Top: `tt_um_mcranny_protocol_emulator_v3`, instantiates everything. |
| `src/v3_opcodes.vh` | Generated opcode constants and the `legal()` function. Never hand-edited. |
| `tools/v3_opcode_header.py` | Generates `src/v3_opcodes.vh` from `opcodes.py`. |
| `tools/v3_mutations.py` | Mutation gate. |
| `tools/v3_hardening.py` | Local LibreLane run driver and signoff report. |
| `formal/v3_*.ys` | One proof script per module plus the integration proof. |
| `test/v3/*.py` | cocotb test modules. |
| `test/support/v3_*.py` | cocotb harnesses. |
| `fpga/arty_a7_35t/` | FPGA top, BRAM store, MMCM, constraints. |
| `physical/v3/` | `info.yaml`, `config.json`, `overrides.json`, macro configuration. |

Modified: `pyproject.toml`, `info.yaml`, `docs/index.md`, `docs/conf.py`, `Makefile`, `test/Makefile`.

Removed in Task 2 (requires explicit confirmation first): V1 and V2 RTL, tests, tooling and docs.

---

## Phase 0 — Environment and repository

### Task 1: Line endings, V3 package skeleton, type checking

Establishes the Python package V3 code lands in, and pins line endings so provenance hashes are
stable across Windows and WSL.

**Files:**
- Create: `.gitattributes`
- Create: `host/protocol_emulator/v3/__init__.py`
- Create: `tests/v3/__init__.py`
- Create: `mypy.ini`
- Modify: `pyproject.toml`
- Create: `tests/v3/test_package.py`

**Interfaces:**
- Produces: the importable package `protocol_emulator.v3` with `__version__: str = "3.0.0.dev0"`.

- [ ] **Step 1: Write the failing test**

Create `tests/v3/__init__.py` as an empty file, then `tests/v3/test_package.py`:

```python
"""The V3 package exists, is importable, and declares its version."""
import protocol_emulator.v3 as v3


def test_version_is_a_v3_dev_version() -> None:
    assert v3.__version__.startswith("3.")


def test_clock_hz_is_48_mhz() -> None:
    assert v3.CLOCK_HZ == 48_000_000
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_package.py -v
```

Expected: `ModuleNotFoundError: No module named 'protocol_emulator.v3'`.

- [ ] **Step 3: Create the package**

`host/protocol_emulator/v3/__init__.py`:

```python
"""V3 protocol emulator: time-cursor architecture, SRAM program memory, 48 MHz.

V3 is a clean break from V1 and V2. Nothing in this package is encoding- or
cycle-compatible with the earlier designs.
"""

__version__ = "3.0.0.dev0"

#: System clock. Every timing constant in :mod:`protocol_emulator.v3.timing`
#: derives from this value.
CLOCK_HZ = 48_000_000

#: Program memory depth per engine, in 32-bit words.
PROGRAM_WORDS = 256

#: Width of the shared free-running timebase counter.
TIMEBASE_BITS = 24

#: Number of protocol pins.
PIN_COUNT = 8

__all__ = ["CLOCK_HZ", "PROGRAM_WORDS", "TIMEBASE_BITS", "PIN_COUNT", "__version__"]
```

- [ ] **Step 4: Run the test and confirm it passes**

```bash
python -m pytest tests/v3/test_package.py -v
```

Expected: 2 passed.

- [ ] **Step 5: Pin line endings**

Create `.gitattributes`:

```text
# Keep LF in the working tree on every platform. The Linux flow runs these
# scripts, and provenance hashes must match between Windows and WSL.
* text=auto eol=lf
*.png binary
*.gds binary
*.gds.gz binary
*.jpg binary
*.pdf binary
```

- [ ] **Step 6: Confirm no file content changes**

```bash
git add --renormalize .
git status --short
```

Expected: only `.gitattributes` listed. The clone already used `core.autocrlf=false`, so no
existing file should be rewritten. If any source file appears, stop and investigate before
committing.

- [ ] **Step 7: Add mypy configuration**

Create `mypy.ini`:

```ini
[mypy]
python_version = 3.11
mypy_path = host
packages = protocol_emulator.v3
strict = True
warn_unreachable = True
```

Add to `pyproject.toml` under `[project.optional-dependencies]`:

```toml
dev = ["mypy==1.11.2", "pytest==8.3.3"]
```

- [ ] **Step 8: Run mypy**

```bash
python -m mypy --config-file mypy.ini
```

Expected: `Success: no issues found in 1 source file`.

- [ ] **Step 9: Commit**

```bash
git add .gitattributes mypy.ini pyproject.toml host/protocol_emulator/v3 tests/v3
git commit -m "Add V3 package skeleton, LF normalization, and strict mypy"
git log -1 --format='%an <%ae>%n%B'
```

Expected: author `Matthew Cranny <crannymatthew@gmail.com>` and no trailers.

---

### Task 2: Retire V1 and V2 from the working tree

**Stop. This task deletes tracked files. Get explicit confirmation from Matthew in the session
before running any `git rm`.** V2 stays reachable through the `v2` tag and its release assets; this
only clears the working tree so V3 is unambiguous. Do not delete the `v2` tag. Do not force-push
anything, ever.

**Files:**
- Delete: `src/engine.v`, `src/project.v`, `src/v2_engine.v`, `src/v2_project.v`,
  `src/v2_capture.v`, `src/v2_input_filter.v`, `src/host_spi.v`
- Delete: `host/protocol_emulator/isa.py`, `model.py`, `firmware.py`, `host.py`,
  `host/protocol_emulator/v2/`
- Delete: `tests/v1/`, `tests/v2/`, `test/v1/`, `test/v2/`, `test/support/v1_engine.py`,
  `test/support/v2_engine.py`, `test/support/v2_pins.py`, `test/tb.v`, `test/reset_tb.v`
- Delete: `test/Makefile.core`, `Makefile.v1pins`, `Makefile.v2`, `Makefile.v2capture`,
  `Makefile.v2pins`, `Makefile.reset`
- Delete: `formal/engine.ys`, `formal/v2_*.ys`
- Delete: `tools/v2_*.py`, `tools/check_v2.py`
- Delete: `physical/v1/`, `physical/v2/`
- Delete: `examples/v2/`, `examples/pulse.asm`, `examples/uart_115200.asm`,
  `examples/uart_1000000.asm`
- Delete: `docs/v1-*.md`, `docs/v2-*.md`, `docs/verification-track-2.md`
- Keep: `evidence/` (historical record), `docs/release-notes.md`, `docs/release-evidence.md`,
  `tools/gds_viewer*.py`, `tools/render_gds.py`, `tools/export_gds_static.py`,
  `tools/collect_evidence.py`, `tools/validate_reset.py`, `tools/audit_initialization.py`
- Modify: `docs/index.md`, `Makefile`, `test/Makefile`, `pyproject.toml`

**Interfaces:**
- Produces: a tree where `src/` contains only V3 RTL, and `protocol_emulator` exposes only `cli`
  and `v3`.

- [ ] **Step 1: Record what is being removed**

```bash
git rev-parse v2
git ls-files src host tests test formal tools physical examples docs > build/pre-removal-manifest.txt
```

Expected: the `v2` tag resolves to a commit hash. Write that hash into the commit message in
Step 7 so the removal is traceable.

- [ ] **Step 2: Confirm the tag is intact and reachable**

```bash
git tag --list v2
git cat-file -t v2
```

Expected: `v2` listed, and a type of `tag` or `commit`. If the tag is missing, stop — the history
is the only remaining copy and removal must not proceed.

- [ ] **Step 3: Remove the files**

```bash
git rm -r --quiet src/engine.v src/project.v src/host_spi.v src/v2_engine.v src/v2_project.v \
  src/v2_capture.v src/v2_input_filter.v \
  host/protocol_emulator/isa.py host/protocol_emulator/model.py \
  host/protocol_emulator/firmware.py host/protocol_emulator/host.py \
  host/protocol_emulator/v2 \
  tests/v1 tests/v2 test/v1 test/v2 \
  test/support/v1_engine.py test/support/v2_engine.py test/support/v2_pins.py \
  test/tb.v test/reset_tb.v test/tb.gtkw \
  test/Makefile.core test/Makefile.v1pins test/Makefile.v2 \
  test/Makefile.v2capture test/Makefile.v2pins test/Makefile.reset \
  formal/engine.ys formal/v2_capture.ys formal/v2_engine.ys \
  formal/v2_input_filter.ys formal/v2_integration.ys \
  tools/check_v2.py \
  physical/v1 physical/v2 \
  examples/v2 examples/pulse.asm examples/uart_115200.asm examples/uart_1000000.asm \
  docs/v1-compatibility.md docs/v1-specification.md docs/v2-architecture.md \
  docs/v2-capture.md docs/v2-design-evolution.md docs/v2-replay.md docs/v2-scenarios.md \
  docs/v2-tt-flow.md docs/v2-validation.md docs/verification-track-2.md
git rm -r --quiet $(git ls-files 'tools/v2_*.py')
```

- [ ] **Step 4: Remove the tests that covered deleted tooling**

```bash
git rm -r --quiet $(git ls-files 'tests/tooling/test_v2_*.py')
```

Then open `tests/tooling/` and remove any remaining test that imports a deleted module. Find them
with:

```bash
python -m pytest tests/tooling -q --collect-only
```

Expected after cleanup: collection succeeds with no `ImportError`.

- [ ] **Step 5: Update `docs/index.md` and the Makefiles**

In `docs/index.md`, remove every toctree entry naming a deleted file. The docs build is strict
(`sphinx -n -W`), so a dangling entry is a hard error.

In `test/Makefile`, remove targets that reference deleted Makefiles. In the root `Makefile`, remove
targets referencing deleted tools.

In `pyproject.toml`, bump:

```toml
version = "3.0.0.dev0"
description = "Toolchain and model for the V3 protocol emulator ASIC"
```

- [ ] **Step 6: Verify the tree builds and tests collect**

```bash
python -m pytest tests -q
python -m sphinx -n -W -q -b html docs build/docs-check
```

Expected: pytest passes with the remaining tooling tests, and Sphinx exits 0.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "Retire V1 and V2 from the working tree" \
  -m "V3 is a clean architectural break. V1/V2 RTL, Python, tests, formal scripts, physical configs and docs are removed from the default tree. They remain available at tag v2 (<hash from Step 1>) and in history."
git log -1 --format='%an <%ae>%n%B'
```

---

## Phase 1 — Opcode table, assembler, timing

This phase produces a working assembler with no simulator, no RTL and no hardware. It is pure
Python and fully testable on Windows.

### Task 3: The opcode table

One table is the source of truth for the assembler, the model, and the generated Verilog legality
check. Everything downstream reads this file.

**Files:**
- Create: `host/protocol_emulator/v3/opcodes.py`
- Test: `tests/v3/test_opcodes.py`

**Interfaces:**
- Produces:
  - `class Field(NamedTuple)` with `name: str`, `width: int`, `shift: int`.
  - `class Op(NamedTuple)` with `name: str`, `code: int`, `fields: tuple[Field, ...]`,
    `timing: str`.
  - `OPS: dict[str, Op]` keyed by uppercase mnemonic.
  - `BY_CODE: dict[int, Op]`.
  - `CONDITIONS: dict[str, int]`.
  - `operand_mask(op: Op) -> int` returning the mask of operand bits the opcode defines; bits
    outside it must be zero.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_opcodes.py`:

```python
"""The opcode table is internally consistent and fits the 32-bit word."""
import pytest

from protocol_emulator.v3 import opcodes


def test_every_opcode_fits_six_bits() -> None:
    for op in opcodes.OPS.values():
        assert 0 <= op.code < 64, op.name


def test_opcodes_are_unique() -> None:
    codes = [op.code for op in opcodes.OPS.values()]
    assert len(codes) == len(set(codes))


def test_operand_fields_fit_26_bits_and_do_not_overlap() -> None:
    for op in opcodes.OPS.values():
        used = 0
        for field in op.fields:
            span = ((1 << field.width) - 1) << field.shift
            assert span <= 0x3FFFFFF, f"{op.name}.{field.name} exceeds the operand field"
            assert not used & span, f"{op.name}.{field.name} overlaps another field"
            used |= span
        assert used == opcodes.operand_mask(op)


def test_by_code_round_trips() -> None:
    for op in opcodes.OPS.values():
        assert opcodes.BY_CODE[op.code] is op


def test_timing_classes_are_known() -> None:
    allowed = {"single", "branch", "stall"}
    for op in opcodes.OPS.values():
        assert op.timing in allowed, op.name


def test_required_instructions_are_present() -> None:
    required = {
        "NOP", "HALT", "JMP", "BRC", "DJNZ", "CALL", "RET", "FAIL",
        "LI", "MOV", "ADD", "SUB", "AND", "OR", "XOR", "SHL", "SHR", "BREQ",
        "ADVANCE", "ADVANCER", "CURSORSET", "CURSORGET", "NOW", "WAITTIME",
        "SET", "OE", "DRIVE", "OUT",
        "SAMPLE", "IN", "EDGETIME", "WAITEDGE", "WAITFOR", "MOVIS", "CLEARIS",
        "PULL", "PULLNB", "RX",
        "MARK", "WAITEVENT", "CLEAREVENT", "CLEARMISS",
        "CRCCFG", "CRCLOAD", "CRCBIT", "CRCGET",
    }
    assert required <= set(opcodes.OPS)


def test_commit_instructions_all_carry_a_ten_bit_offset() -> None:
    for name in ("SET", "OE", "DRIVE", "OUT"):
        offset = next(f for f in opcodes.OPS[name].fields if f.name == "offset")
        assert offset.width == 10
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_opcodes.py -v
```

Expected: `ModuleNotFoundError: No module named 'protocol_emulator.v3.opcodes'`.

- [ ] **Step 3: Write the table**

`host/protocol_emulator/v3/opcodes.py`:

```python
"""The V3 opcode table. Single source of truth.

The assembler, the cycle-accurate model and the generated Verilog legality
check all derive from this table. Changing an encoding here changes all three;
``tests/v3/test_opcode_header.py`` fails if the generated Verilog drifts.

Word layout::

    31          26 25                             0
    +-------------+-------------------------------+
    |   opcode    |            operand            |
    +-------------+-------------------------------+

Operand fields are packed from bit 0 upward in declaration order. Bits outside
a declared field must be zero; a word with a set reserved bit is illegal.

Timing classes:

``single``
    Retires in one cycle. The prefetched next word is used.
``branch``
    May redirect the fetch. A taken branch costs exactly two cycles.
``stall``
    May hold the engine for an unbounded or input-dependent number of cycles.
"""
from typing import NamedTuple


class Field(NamedTuple):
    """One operand field, packed at ``shift`` and ``width`` bits wide."""

    name: str
    width: int
    shift: int


class Op(NamedTuple):
    """One instruction: mnemonic, 6-bit opcode, operand layout, timing class."""

    name: str
    code: int
    fields: tuple[Field, ...]
    timing: str


def _op(name: str, code: int, timing: str, *fields: tuple[str, int]) -> Op:
    packed: list[Field] = []
    shift = 0
    for field_name, width in fields:
        packed.append(Field(field_name, width, shift))
        shift += width
    if shift > 26:
        raise ValueError(f"{name} operand fields need {shift} bits; only 26 are available")
    return Op(name, code, tuple(packed), timing)


#: Branch conditions for ``BRC``.
CONDITIONS = {
    "timeout": 0,
    "interrupted": 1,
    "missed": 2,
    "tx_empty": 3,
    "rx_full": 4,
    "pull_empty": 5,
    "event0": 6,
    "event1": 7,
    "zero": 8,
    "carry": 9,
}

#: Edge polarities for ``WAITEDGE``.
POLARITIES = {"rising": 0, "falling": 1, "either": 2}

_TABLE = [
    # --- control -------------------------------------------------------
    _op("NOP", 0x00, "single"),
    _op("HALT", 0x01, "single"),
    _op("JMP", 0x02, "branch", ("target", 8)),
    _op("BRC", 0x03, "branch", ("target", 8), ("condition", 4)),
    _op("DJNZ", 0x04, "branch", ("target", 8), ("reg", 2)),
    _op("CALL", 0x05, "branch", ("target", 8)),
    _op("RET", 0x06, "branch"),
    _op("FAIL", 0x07, "single", ("code", 8)),
    # --- registers -----------------------------------------------------
    _op("LI", 0x08, "single", ("reg", 2), ("value", 24)),
    _op("MOV", 0x09, "single", ("dst", 2), ("src", 2)),
    _op("ADD", 0x0A, "single", ("dst", 2), ("src", 2)),
    _op("SUB", 0x0B, "single", ("dst", 2), ("src", 2)),
    _op("AND", 0x0C, "single", ("dst", 2), ("src", 2)),
    _op("OR", 0x0D, "single", ("dst", 2), ("src", 2)),
    _op("XOR", 0x0E, "single", ("dst", 2), ("src", 2)),
    _op("SHL", 0x0F, "single", ("dst", 2), ("amount", 5)),
    _op("SHR", 0x10, "single", ("dst", 2), ("amount", 5)),
    _op("BREQ", 0x11, "branch", ("target", 8), ("reg", 2), ("value", 8)),
    # --- time ----------------------------------------------------------
    _op("ADVANCE", 0x14, "single", ("ticks", 24)),
    _op("ADVANCER", 0x15, "single", ("reg", 2)),
    _op("CURSORSET", 0x16, "single", ("reg", 2)),
    _op("CURSORGET", 0x17, "single", ("reg", 2)),
    _op("NOW", 0x18, "single", ("reg", 2)),
    _op("WAITTIME", 0x19, "stall"),
    # --- pin commits ---------------------------------------------------
    _op("SET", 0x1C, "stall", ("mask", 8), ("value", 8), ("offset", 10)),
    _op("OE", 0x1D, "stall", ("mask", 8), ("oe", 8), ("offset", 10)),
    _op("DRIVE", 0x1E, "stall", ("value", 8), ("oe", 8), ("offset", 10)),
    _op("OUT", 0x1F, "stall", ("pin", 3), ("level", 1), ("offset", 10)),
    # --- inputs --------------------------------------------------------
    _op("SAMPLE", 0x20, "single", ("reg", 2)),
    _op("IN", 0x21, "single", ("pin", 3), ("msb_first", 1)),
    _op("EDGETIME", 0x22, "single", ("pin", 3), ("reg", 2)),
    _op("WAITEDGE", 0x23, "stall", ("pin", 3), ("polarity", 2)),
    _op("WAITFOR", 0x24, "stall", ("pin", 3), ("level", 1)),
    _op("MOVIS", 0x25, "single", ("reg", 2)),
    _op("CLEARIS", 0x26, "single"),
    # --- queues --------------------------------------------------------
    _op("PULL", 0x28, "stall", ("reg", 2)),
    _op("PULLNB", 0x29, "single", ("reg", 2)),
    _op("RX", 0x2A, "single", ("meta", 8)),
    # --- events --------------------------------------------------------
    _op("MARK", 0x2C, "single", ("code", 8)),
    _op("WAITEVENT", 0x2D, "stall", ("mask", 8)),
    _op("CLEAREVENT", 0x2E, "single", ("mask", 8)),
    _op("CLEARMISS", 0x2F, "single", ("mask", 8)),
    # --- CRC -----------------------------------------------------------
    _op("CRCCFG", 0x30, "single", ("poly", 16), ("msb_first", 1)),
    _op("CRCLOAD", 0x31, "single", ("reg", 2)),
    _op("CRCBIT", 0x32, "single", ("reg", 2)),
    _op("CRCGET", 0x33, "single", ("reg", 2)),
]

#: Mnemonic to :class:`Op`.
OPS: dict[str, Op] = {op.name: op for op in _TABLE}

#: Opcode number to :class:`Op`.
BY_CODE: dict[int, Op] = {op.code: op for op in _TABLE}


def operand_mask(op: Op) -> int:
    """Return the mask of operand bits ``op`` defines.

    Bits set outside this mask make the instruction word illegal.
    """
    mask = 0
    for field in op.fields:
        mask |= ((1 << field.width) - 1) << field.shift
    return mask
```

- [ ] **Step 4: Run the tests**

```bash
python -m pytest tests/v3/test_opcodes.py -v
python -m mypy --config-file mypy.ini
```

Expected: 7 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add host/protocol_emulator/v3/opcodes.py tests/v3/test_opcodes.py
git commit -m "Add the V3 opcode table"
```

---

### Task 4: Timing constants

**Files:**
- Create: `host/protocol_emulator/v3/timing.py`
- Test: `tests/v3/test_timing.py`

**Interfaces:**
- Consumes: `protocol_emulator.v3.CLOCK_HZ`.
- Produces:
  - `cycles(seconds: float, tolerance: float = 0.001) -> int` — rounds to the nearest cycle and
    raises `ValueError` if the relative rate error exceeds `tolerance`.
  - `bit_cycles(baud: int, tolerance: float = 0.001) -> int`.
  - `rate_error(seconds: float) -> float` — signed relative error of the rounded result.
  - `UART: dict[int, int]`, `SPI_HALF: dict[int, int]`, `I2C: dict[int, tuple[int, int]]`,
    `WS2812: dict[str, tuple[int, int]]`, `WS2812_RESET: int`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_timing.py`:

```python
"""Timing constants derive from 48 MHz and refuse silent rounding."""
import pytest

from protocol_emulator.v3 import timing


def test_exact_baud_rates_are_exact() -> None:
    assert timing.bit_cycles(9600) == 5000
    assert timing.bit_cycles(1_000_000) == 48


def test_115200_is_accepted_within_tolerance() -> None:
    assert timing.bit_cycles(115200) == 417
    assert abs(timing.rate_error(1 / 115200)) < 0.001


def test_a_rate_outside_tolerance_is_rejected() -> None:
    # 20 Mbaud is 2.4 cycles per bit; rounding to 2 is a 20% error.
    with pytest.raises(ValueError, match="rate error"):
        timing.bit_cycles(20_000_000)


def test_i2c_100k_meets_the_bus_minimums() -> None:
    low, high = timing.I2C[100_000]
    assert low == 240 and high == 240
    assert low / timing.CLOCK_HZ >= 4.7e-6
    assert high / timing.CLOCK_HZ >= 4.0e-6


def test_i2c_400k_keeps_margin_over_the_bus_minimums() -> None:
    low, high = timing.I2C[400_000]
    assert (low, high) == (72, 48)
    assert low / timing.CLOCK_HZ >= 1.3e-6
    # 1.0 us against a 0.6 us minimum: 400 ns of headroom for open-drain rise.
    assert high / timing.CLOCK_HZ - 0.6e-6 >= 390e-9


def test_ws2812_pulse_widths_land_inside_the_datasheet_windows() -> None:
    zero_high, zero_low = timing.WS2812["0"]
    one_high, one_low = timing.WS2812["1"]
    assert (zero_high, zero_low) == (19, 41)
    assert (one_high, one_low) == (38, 22)
    # Datasheet: T0H 0.4 us +/- 150 ns, T1H 0.8 us +/- 150 ns.
    assert abs(zero_high / timing.CLOCK_HZ - 0.4e-6) < 150e-9
    assert abs(one_high / timing.CLOCK_HZ - 0.8e-6) < 150e-9
    # Every bit is the same 1.25 us period.
    assert zero_high + zero_low == one_high + one_low == 60


def test_ws2812_reset_is_at_least_300_us() -> None:
    assert timing.WS2812_RESET >= 300e-6 * timing.CLOCK_HZ


def test_spi_half_periods_are_exact() -> None:
    assert timing.SPI_HALF[1_000_000] == 24
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_timing.py -v
```

Expected: `ModuleNotFoundError`.

- [ ] **Step 3: Write the module**

`host/protocol_emulator/v3/timing.py`:

```python
"""Protocol timing constants at 48 MHz.

Every value here is computed from :data:`protocol_emulator.v3.CLOCK_HZ`. None
are written as literals in firmware. :func:`cycles` refuses a conversion whose
rounding error exceeds the caller's tolerance, so an unachievable rate fails at
assembly time instead of producing a chip that is quietly off-spec.
"""
from . import CLOCK_HZ

TICK_SECONDS = 1.0 / CLOCK_HZ


def rate_error(seconds: float) -> float:
    """Signed relative error introduced by rounding ``seconds`` to whole cycles."""
    if seconds <= 0:
        raise ValueError("interval must be positive")
    exact = seconds * CLOCK_HZ
    rounded = round(exact)
    if rounded == 0:
        raise ValueError(f"{seconds} s is shorter than one clock cycle")
    return (rounded - exact) / exact


def cycles(seconds: float, tolerance: float = 0.001) -> int:
    """Convert ``seconds`` to whole clock cycles.

    :param seconds: the interval to convert.
    :param tolerance: the largest acceptable relative error, default 0.1%.
    :raises ValueError: if rounding would exceed ``tolerance``, or the interval
        is shorter than one cycle.
    """
    error = rate_error(seconds)
    if abs(error) > tolerance:
        raise ValueError(
            f"{seconds} s is {error:+.3%} rate error at {CLOCK_HZ} Hz, "
            f"over the {tolerance:.3%} tolerance"
        )
    return round(seconds * CLOCK_HZ)


def bit_cycles(baud: int, tolerance: float = 0.001) -> int:
    """Cycles per bit for ``baud``, rejecting rates that cannot be hit."""
    return cycles(1.0 / baud, tolerance)


#: Cycles per UART bit.
UART = {rate: bit_cycles(rate) for rate in (9600, 115200, 1_000_000)}

#: Cycles per SPI half period (one clock phase).
SPI_HALF = {rate: cycles(1.0 / (2 * rate)) for rate in (1_000_000,)}

#: I2C SCL ``(low, high)`` cycles.
#:
#: 100 kHz uses a symmetric 5.0 us split against minimums of 4.7 and 4.0 us.
#: 400 kHz uses 1.5 / 1.0 us against minimums of 1.3 and 0.6 us. The asymmetry
#: is deliberate: it leaves 400 ns of high-time headroom for the open-drain
#: rise, rather than meeting the 0.6 us minimum by a few nanoseconds.
I2C = {
    100_000: (cycles(5.0e-6), cycles(5.0e-6)),
    400_000: (cycles(1.5e-6), cycles(1.0e-6)),
}

#: WS2812 ``(high, low)`` cycles per symbol. Both symbols share a 1.25 us period.
#: Both symbols are 19.2 and 38.4 cycles exactly, so each rounds with a 1.04%
#: error. That is far outside the default tolerance and well inside the
#: datasheet's +/-150 ns window, so the tolerance is widened here and only here.
_WS2812_TOLERANCE = 0.02
WS2812 = {
    "0": (
        cycles(0.4e-6, _WS2812_TOLERANCE),
        cycles(1.25e-6) - cycles(0.4e-6, _WS2812_TOLERANCE),
    ),
    "1": (
        cycles(0.8e-6, _WS2812_TOLERANCE),
        cycles(1.25e-6) - cycles(0.8e-6, _WS2812_TOLERANCE),
    ),
}

#: WS2812 latch: hold the line low for at least this many cycles.
WS2812_RESET = cycles(300e-6)
```

- [ ] **Step 4: Run and verify the derived values**

```bash
python -m pytest tests/v3/test_timing.py -v
python -c "from protocol_emulator.v3 import timing; print(timing.UART, timing.I2C, timing.WS2812)"
```

Expected: 8 passed, and printed values matching the spec table —
`{9600: 5000, 115200: 417, 1000000: 48}`, `{100000: (240, 240), 400000: (72, 48)}`,
`{'0': (19, 41), '1': (38, 22)}`.

If `WS2812["0"]` does not come out `(19, 41)`, the tolerance on `cycles(0.4e-6, ...)` is wrong:
0.4 µs is 19.2 cycles, a −1.04% error, so the tolerance must be at least 0.0105. Adjust to the
smallest tolerance that admits it rather than widening the default.

- [ ] **Step 5: Commit**

```bash
git add host/protocol_emulator/v3/timing.py tests/v3/test_timing.py
git commit -m "Add 48 MHz timing constants with rounding-error rejection"
```

---

### Task 5: Encoder and decoder

**Files:**
- Create: `host/protocol_emulator/v3/isa.py`
- Test: `tests/v3/test_isa_codec.py`

**Interfaces:**
- Consumes: `opcodes.OPS`, `opcodes.BY_CODE`, `opcodes.operand_mask`, `opcodes.CONDITIONS`.
- Produces:
  - `class Instruction(NamedTuple)` with `name: str` and `operands: dict[str, int]`.
  - `encode(name: str, **operands: int) -> int`.
  - `decode(word: int) -> Instruction`.
  - `IllegalInstruction(ValueError)`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_isa_codec.py`:

```python
"""Encode and decode are exact inverses, and illegal words are rejected."""
import pytest

from protocol_emulator.v3 import isa, opcodes


def test_nop_is_all_zero() -> None:
    assert isa.encode("NOP") == 0


def test_opcode_lands_in_the_top_six_bits() -> None:
    word = isa.encode("HALT")
    assert word >> 26 == opcodes.OPS["HALT"].code


def test_li_packs_register_then_value() -> None:
    word = isa.encode("LI", reg=2, value=0xABCDEF)
    assert word >> 26 == 0x08
    assert word & 0x3 == 2
    assert (word >> 2) & 0xFFFFFF == 0xABCDEF


def test_every_instruction_round_trips_at_field_maxima() -> None:
    for op in opcodes.OPS.values():
        operands = {field.name: (1 << field.width) - 1 for field in op.fields}
        if op.name == "BRC":
            operands["condition"] = max(opcodes.CONDITIONS.values())
        if op.name == "WAITEDGE":
            operands["polarity"] = max(opcodes.POLARITIES.values())
        word = isa.encode(op.name, **operands)
        back = isa.decode(word)
        assert back.name == op.name
        assert back.operands == operands


def test_every_instruction_round_trips_at_zero() -> None:
    for op in opcodes.OPS.values():
        operands = {field.name: 0 for field in op.fields}
        assert isa.decode(isa.encode(op.name, **operands)).operands == operands


def test_an_out_of_range_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="out of range"):
        isa.encode("OUT", pin=8, level=0, offset=0)


def test_a_missing_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="expects"):
        isa.encode("OUT", pin=0)


def test_an_unexpected_operand_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="expects"):
        isa.encode("NOP", pin=0)


def test_an_unused_opcode_is_illegal() -> None:
    unused = next(code for code in range(64) if code not in opcodes.BY_CODE)
    with pytest.raises(isa.IllegalInstruction, match="unused opcode"):
        isa.decode(unused << 26)


def test_a_set_reserved_bit_is_illegal() -> None:
    # NOP defines no operand fields, so any set operand bit is reserved.
    with pytest.raises(isa.IllegalInstruction, match="reserved"):
        isa.decode(1)


def test_a_reserved_condition_is_illegal() -> None:
    bad = max(opcodes.CONDITIONS.values()) + 1
    word = (opcodes.OPS["BRC"].code << 26) | (bad << 8)
    with pytest.raises(isa.IllegalInstruction, match="condition"):
        isa.decode(word)


def test_a_word_wider_than_32_bits_is_rejected() -> None:
    with pytest.raises(isa.IllegalInstruction, match="32"):
        isa.decode(1 << 32)
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_isa_codec.py -v
```

Expected: `ModuleNotFoundError: No module named 'protocol_emulator.v3.isa'`.

- [ ] **Step 3: Write the codec**

`host/protocol_emulator/v3/isa.py` (the assembler is added in Task 6; write only the codec now):

```python
"""V3 instruction encoding, decoding and assembly.

All encoding behaviour is derived from :mod:`protocol_emulator.v3.opcodes`.
There are no per-instruction special cases in this module: adding an
instruction means adding a row to the table.
"""
from typing import NamedTuple

from . import opcodes
from .opcodes import CONDITIONS, POLARITIES

WORD_BITS = 32
OPCODE_SHIFT = 26
OPERAND_BITS = OPCODE_SHIFT


class IllegalInstruction(ValueError):
    """An instruction cannot be encoded, or a word is not a legal instruction."""


class Instruction(NamedTuple):
    """A decoded instruction: mnemonic plus named operand values."""

    name: str
    operands: dict[str, int]


def encode(name: str, **operands: int) -> int:
    """Encode ``name`` with the given named operands into a 32-bit word.

    :raises IllegalInstruction: on an unknown mnemonic, a wrong operand set, or
        an operand too wide for its field.
    """
    key = name.upper()
    op = opcodes.OPS.get(key)
    if op is None:
        raise IllegalInstruction(f"unknown instruction {name!r}")
    expected = {field.name for field in op.fields}
    if set(operands) != expected:
        raise IllegalInstruction(
            f"{key} expects operands {sorted(expected)}, got {sorted(operands)}"
        )
    word = op.code << OPCODE_SHIFT
    for field in op.fields:
        value = operands[field.name]
        limit = (1 << field.width) - 1
        if not 0 <= value <= limit:
            raise IllegalInstruction(
                f"{key}.{field.name}={value} is out of range 0..{limit}"
            )
        word |= value << field.shift
    return word


def decode(word: int) -> Instruction:
    """Decode a 32-bit word.

    :raises IllegalInstruction: if the word is too wide, uses an unused opcode,
        sets a reserved operand bit, or carries a reserved enumerated value.
    """
    if not 0 <= word < (1 << WORD_BITS):
        raise IllegalInstruction(f"instruction word must fit in {WORD_BITS} bits")
    code = word >> OPCODE_SHIFT
    operand = word & ((1 << OPERAND_BITS) - 1)
    op = opcodes.BY_CODE.get(code)
    if op is None:
        raise IllegalInstruction(f"unused opcode 0x{code:02X}")
    if operand & ~opcodes.operand_mask(op):
        raise IllegalInstruction(f"{op.name} has a reserved operand bit set")
    values = {
        field.name: (operand >> field.shift) & ((1 << field.width) - 1)
        for field in op.fields
    }
    if op.name == "BRC" and values["condition"] not in CONDITIONS.values():
        raise IllegalInstruction(f"reserved condition {values['condition']}")
    if op.name == "WAITEDGE" and values["polarity"] not in POLARITIES.values():
        raise IllegalInstruction(f"reserved polarity {values['polarity']}")
    return Instruction(op.name, values)


def is_legal(word: int) -> bool:
    """Return whether ``word`` decodes. Mirrors ``legal()`` in the RTL."""
    try:
        decode(word)
    except IllegalInstruction:
        return False
    return True
```

- [ ] **Step 4: Run and confirm green**

```bash
python -m pytest tests/v3/test_isa_codec.py -v
python -m mypy --config-file mypy.ini
```

Expected: 12 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add host/protocol_emulator/v3/isa.py tests/v3/test_isa_codec.py
git commit -m "Add the V3 instruction codec driven by the opcode table"
```

---

### Task 6: Assembler

**Files:**
- Modify: `host/protocol_emulator/v3/isa.py` (append)
- Test: `tests/v3/test_assembler.py`

**Interfaces:**
- Consumes: `encode`, `IllegalInstruction`, `CONDITIONS`, `POLARITIES`,
  `protocol_emulator.v3.PROGRAM_WORDS`.
- Produces:
  - `class Image(NamedTuple)` with `words: tuple[int, ...]` (always 256 long),
    `labels: dict[str, int]`, `used: int`.
  - `assemble(source: str) -> Image`.
  - `AssemblyError(ValueError)`.

Syntax: one instruction per line. `#` starts a comment. A line ending in `:` is a label. Operands
are `name=value` pairs, comma- or space-separated, in any order. Values are decimal, `0x` hex,
`0b` binary, a label name, or a symbolic condition or polarity name. The directive `.org N` sets
the assembly address. Unused words are filled with `HALT`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_assembler.py`:

```python
"""The assembler produces a full 256-word image with resolved labels."""
import pytest

from protocol_emulator.v3 import PROGRAM_WORDS, isa


def test_an_empty_program_is_all_halt() -> None:
    image = isa.assemble("")
    assert len(image.words) == PROGRAM_WORDS
    assert set(image.words) == {isa.encode("HALT")}
    assert image.used == 0


def test_instructions_land_in_order_and_the_rest_is_halt() -> None:
    image = isa.assemble("NOP\nHALT\n")
    assert image.words[0] == isa.encode("NOP")
    assert image.words[1] == isa.encode("HALT")
    assert image.used == 2
    assert image.words[2] == isa.encode("HALT")


def test_comments_and_blank_lines_are_ignored() -> None:
    image = isa.assemble("# leading comment\n\nNOP   # trailing\n")
    assert image.used == 1


def test_a_backward_label_resolves() -> None:
    image = isa.assemble("top:\nNOP\nJMP target=top\n")
    assert isa.decode(image.words[1]).operands["target"] == 0


def test_a_forward_label_resolves() -> None:
    image = isa.assemble("JMP target=end\nNOP\nend:\nHALT\n")
    assert isa.decode(image.words[0]).operands["target"] == 2


def test_symbolic_conditions_resolve() -> None:
    image = isa.assemble("BRC target=0 condition=timeout\n")
    assert isa.decode(image.words[0]).operands["condition"] == 0


def test_symbolic_polarities_resolve() -> None:
    image = isa.assemble("WAITEDGE pin=3 polarity=falling\n")
    assert isa.decode(image.words[0]).operands["polarity"] == 1


def test_hex_and_binary_literals_resolve() -> None:
    image = isa.assemble("LI reg=0 value=0xFF\nLI reg=1 value=0b1010\n")
    assert isa.decode(image.words[0]).operands["value"] == 0xFF
    assert isa.decode(image.words[1]).operands["value"] == 0b1010


def test_operand_order_does_not_matter() -> None:
    a = isa.assemble("OUT offset=5 level=1 pin=2\n")
    b = isa.assemble("OUT pin=2 level=1 offset=5\n")
    assert a.words[0] == b.words[0]


def test_org_moves_the_assembly_address() -> None:
    image = isa.assemble(".org 10\nNOP\n")
    assert image.words[10] == isa.encode("NOP")
    assert image.words[0] == isa.encode("HALT")


def test_a_program_that_does_not_fit_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="does not fit"):
        isa.assemble("NOP\n" * (PROGRAM_WORDS + 1))


def test_org_past_the_end_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="does not fit"):
        isa.assemble(".org 255\nNOP\nNOP\n")


def test_a_duplicate_label_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="duplicate label"):
        isa.assemble("a:\nNOP\na:\nNOP\n")


def test_an_undefined_label_is_rejected() -> None:
    with pytest.raises(isa.AssemblyError, match="undefined"):
        isa.assemble("JMP target=nowhere\n")


def test_an_error_reports_the_line_number() -> None:
    with pytest.raises(isa.AssemblyError, match="line 3"):
        isa.assemble("NOP\nNOP\nBOGUS\n")


def test_two_instructions_cannot_occupy_the_same_address() -> None:
    with pytest.raises(isa.AssemblyError, match="already occupied"):
        isa.assemble("NOP\n.org 0\nNOP\n")
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_assembler.py -v
```

Expected: `AttributeError: module 'protocol_emulator.v3.isa' has no attribute 'assemble'`.

- [ ] **Step 3: Append the assembler to `isa.py`**

```python
class AssemblyError(ValueError):
    """A source program could not be assembled."""


class Image(NamedTuple):
    """An assembled program.

    :ivar words: exactly :data:`protocol_emulator.v3.PROGRAM_WORDS` words, with
        unwritten addresses filled with ``HALT``.
    :ivar labels: label name to instruction address.
    :ivar used: the number of addresses written by the source.
    """

    words: tuple[int, ...]
    labels: dict[str, int]
    used: int


def _tokenize(source: str) -> list[tuple[int, str]]:
    """Return ``(line_number, text)`` for every non-blank, non-comment line."""
    lines = []
    for number, raw in enumerate(source.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if text:
            lines.append((number, text))
    return lines


def _resolve(token: str, labels: dict[str, int], field: str, line: int) -> int:
    """Resolve one operand token to an integer."""
    if field == "condition" and token in CONDITIONS:
        return CONDITIONS[token]
    if field == "polarity" and token in POLARITIES:
        return POLARITIES[token]
    try:
        return int(token, 0)
    except ValueError:
        pass
    if token in labels:
        return labels[token]
    raise AssemblyError(f"line {line}: undefined label or value {token!r}")


def assemble(source: str) -> Image:
    """Assemble ``source`` into a full program image.

    Two passes: the first records label addresses, the second encodes. Both
    honour ``.org``. Addresses not written by the source are filled with
    ``HALT`` so the loader can write all 256 words and the validity bitmap can
    be fully set.

    :raises AssemblyError: on a syntax error, a duplicate or undefined label,
        two instructions at one address, or a program larger than 256 words.
    """
    lines = _tokenize(source)

    labels: dict[str, int] = {}
    address = 0
    for number, text in lines:
        if text.endswith(":"):
            name = text[:-1].strip()
            if name in labels:
                raise AssemblyError(f"line {number}: duplicate label {name!r}")
            labels[name] = address
            continue
        if text.startswith(".org"):
            address = _org_address(text, number)
            continue
        address += 1
    if address > PROGRAM_WORDS:
        raise AssemblyError(
            f"program does not fit: needs {address} words, {PROGRAM_WORDS} available"
        )

    words: list[int | None] = [None] * PROGRAM_WORDS
    address = 0
    for number, text in lines:
        if text.endswith(":"):
            continue
        if text.startswith(".org"):
            address = _org_address(text, number)
            continue
        if address >= PROGRAM_WORDS:
            raise AssemblyError(
                f"line {number}: program does not fit in {PROGRAM_WORDS} words"
            )
        if words[address] is not None:
            raise AssemblyError(f"line {number}: address {address} is already occupied")
        words[address] = _encode_line(text, labels, number)
        address += 1

    used = sum(1 for word in words if word is not None)
    halt = encode("HALT")
    return Image(tuple(halt if word is None else word for word in words), labels, used)


def _org_address(text: str, line: int) -> int:
    parts = text.split()
    if len(parts) != 2:
        raise AssemblyError(f"line {line}: .org takes exactly one address")
    try:
        address = int(parts[1], 0)
    except ValueError:
        raise AssemblyError(f"line {line}: .org address {parts[1]!r} is not a number") from None
    if not 0 <= address < PROGRAM_WORDS:
        raise AssemblyError(
            f"line {line}: program does not fit, .org {address} is outside 0..{PROGRAM_WORDS - 1}"
        )
    return address


def _encode_line(text: str, labels: dict[str, int], line: int) -> int:
    parts = text.replace(",", " ").split()
    mnemonic, assignments = parts[0], parts[1:]
    operands: dict[str, int] = {}
    for assignment in assignments:
        if "=" not in assignment:
            raise AssemblyError(
                f"line {line}: operand {assignment!r} is not in name=value form"
            )
        field, token = assignment.split("=", 1)
        operands[field] = _resolve(token, labels, field, line)
    try:
        return encode(mnemonic, **operands)
    except IllegalInstruction as error:
        raise AssemblyError(f"line {line}: {error}") from None
```

Add `PROGRAM_WORDS` to the import at the top of `isa.py`:

```python
from . import PROGRAM_WORDS, opcodes
```

- [ ] **Step 4: Run and confirm green**

```bash
python -m pytest tests/v3/test_assembler.py -v
python -m mypy --config-file mypy.ini
```

Expected: 16 passed, mypy clean.

- [ ] **Step 5: Commit**

```bash
git add host/protocol_emulator/v3/isa.py tests/v3/test_assembler.py
git commit -m "Add the V3 assembler with labels, .org and full-image fill"
```

---

### Task 7: Generated Verilog opcode header and the drift test

This is the mechanism that keeps the RTL legality check honest. The generator writes
`src/v3_opcodes.vh`; the test regenerates into a temporary file and fails if the committed file
differs.

**Files:**
- Create: `tools/v3_opcode_header.py`
- Create: `src/v3_opcodes.vh` (generated — never hand-edit)
- Test: `tests/v3/test_opcode_header.py`

**Interfaces:**
- Consumes: `opcodes.OPS`, `opcodes.operand_mask`, `opcodes.CONDITIONS`, `opcodes.POLARITIES`.
- Produces: `render() -> str` and a `__main__` entry point writing `src/v3_opcodes.vh`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_opcode_header.py`:

```python
"""The generated Verilog header stays in step with the opcode table."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import v3_opcode_header  # noqa: E402

from protocol_emulator.v3 import opcodes  # noqa: E402

HEADER = ROOT / "src" / "v3_opcodes.vh"


def test_the_committed_header_matches_the_table() -> None:
    assert HEADER.read_text(encoding="utf-8") == v3_opcode_header.render(), (
        "src/v3_opcodes.vh is stale. Regenerate with: "
        "python tools/v3_opcode_header.py"
    )


def test_every_opcode_has_a_define() -> None:
    text = HEADER.read_text(encoding="utf-8")
    for op in opcodes.OPS.values():
        assert f"`define OP_{op.name} 6'h{op.code:02X}" in text


def test_the_header_declares_a_legal_function() -> None:
    text = HEADER.read_text(encoding="utf-8")
    assert "function legal;" in text
    assert "input [31:0] word;" in text


def test_the_header_is_marked_generated() -> None:
    assert "Generated by tools/v3_opcode_header.py" in HEADER.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_opcode_header.py -v
```

Expected: `ModuleNotFoundError: No module named 'v3_opcode_header'`.

- [ ] **Step 3: Write the generator**

`tools/v3_opcode_header.py`:

```python
"""Generate src/v3_opcodes.vh from the Python opcode table.

The RTL must agree with the assembler about which words are legal. Rather than
maintaining two tables, this writes the Verilog one from the Python one.
``tests/v3/test_opcode_header.py`` fails if the committed header drifts.

Run from the repository root::

    python tools/v3_opcode_header.py
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

from protocol_emulator.v3 import opcodes  # noqa: E402

HEADER = ROOT / "src" / "v3_opcodes.vh"


def render() -> str:
    """Return the full text of the generated header."""
    lines = [
        "// Generated by tools/v3_opcode_header.py. Do not edit.",
        "//",
        "// Opcode constants and the instruction legality check, derived from",
        "// host/protocol_emulator/v3/opcodes.py. Regenerate after any table change:",
        "//     python tools/v3_opcode_header.py",
        "",
        "`ifndef V3_OPCODES_VH",
        "`define V3_OPCODES_VH",
        "",
    ]
    for op in sorted(opcodes.OPS.values(), key=lambda entry: entry.code):
        lines.append(f"`define OP_{op.name} 6'h{op.code:02X}")
    lines.append("")
    lines.append("// Operand bits each opcode defines. Any bit outside the mask must be zero.")
    for op in sorted(opcodes.OPS.values(), key=lambda entry: entry.code):
        lines.append(f"`define MASK_{op.name} 26'h{opcodes.operand_mask(op):07X}")
    lines.append("")
    lines.append("// Field positions, for decode in src/v3_engine.v.")
    for op in sorted(opcodes.OPS.values(), key=lambda entry: entry.code):
        for field in op.fields:
            high = field.shift + field.width - 1
            lines.append(f"`define FLD_{op.name}_{field.name.upper()} {high}:{field.shift}")
    lines.append("")
    lines.append(f"`define COND_MAX 4'd{max(opcodes.CONDITIONS.values())}")
    lines.append(f"`define POL_MAX 2'd{max(opcodes.POLARITIES.values())}")
    lines.append("")
    lines.extend(_render_legal())
    lines.append("")
    lines.append("`endif")
    lines.append("")
    return "\n".join(lines)


def _render_legal() -> list[str]:
    """Render the ``legal`` function as a flat case over the 6-bit opcode."""
    lines = [
        "// legal(word) is true when word decodes to a defined instruction with",
        "// no reserved operand bit set and no reserved enumerated value. It is",
        "// the exact counterpart of protocol_emulator.v3.isa.is_legal.",
        "function legal;",
        "    input [31:0] word;",
        "    reg [25:0] operand;",
        "    begin",
        "        operand = word[25:0];",
        "        case (word[31:26])",
    ]
    for op in sorted(opcodes.OPS.values(), key=lambda entry: entry.code):
        guard = f"(operand & ~`MASK_{op.name}) == 26'd0"
        if op.name == "BRC":
            guard += f" && operand[{_field_range(op, 'condition')}] <= `COND_MAX"
        if op.name == "WAITEDGE":
            guard += f" && operand[{_field_range(op, 'polarity')}] <= `POL_MAX"
        lines.append(f"            `OP_{op.name}: legal = {guard};")
    lines += [
        "            default: legal = 1'b0;",
        "        endcase",
        "    end",
        "endfunction",
    ]
    return lines


def _field_range(op: opcodes.Op, name: str) -> str:
    field = next(entry for entry in op.fields if entry.name == name)
    return f"{field.shift + field.width - 1}:{field.shift}"


def main() -> None:
    HEADER.write_text(render(), encoding="utf-8", newline="\n")
    print(f"wrote {HEADER.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the header**

```bash
python tools/v3_opcode_header.py
```

Expected: `wrote src/v3_opcodes.vh`.

- [ ] **Step 5: Run the tests**

```bash
python -m pytest tests/v3/test_opcode_header.py -v
```

Expected: 4 passed.

- [ ] **Step 6: Verify the header actually compiles**

```bash
printf '`include "v3_opcodes.vh"\nmodule t; initial $display("%%b", legal(32'"'"'h0)); endmodule\n' > build/legal_probe.v
iverilog -I src -o build/legal_probe build/legal_probe.v && ./build/legal_probe
```

Expected: `1` (NOP is legal). On Windows without Icarus, defer this step to the first WSL session
and note it in the commit message.

- [ ] **Step 7: Commit**

```bash
git add tools/v3_opcode_header.py src/v3_opcodes.vh tests/v3/test_opcode_header.py
git commit -m "Generate the RTL opcode header from the Python table"
```

---

## Phase 2 — Cycle-accurate model

The model is the reference the RTL is compared against every cycle. Write it before the RTL: it is
faster to get the semantics right in Python, and it becomes the specification the RTL must match.

### Task 8: Timebase and pin cell model

**Files:**
- Create: `host/protocol_emulator/v3/model.py`
- Test: `tests/v3/test_model_pin_cell.py`

**Interfaces:**
- Produces:
  - `TIMEBASE_MASK: int` = `0xFFFFFF`.
  - `signed_distance(due: int, now: int) -> int` — signed 24-bit `due - now`, in
    −8388608..8388607.
  - `class Pending(NamedTuple)` with `due: int`, `value: int`, `oe: int`.
  - `class PinCell` with attributes `value: int`, `oe: int`, `pending: Pending | None`,
    `missed: bool`, `open_drain: bool`, `last_edge: int`, `last_polarity: int`, and methods
    `schedule(due: int, value: int, oe: int, now: int) -> None`, `tick(now: int) -> None`,
    `drive() -> tuple[int, int]`, `observe(level: int, now: int) -> None`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_model_pin_cell.py`:

```python
"""Pin cell semantics: deadlines, misses, open drain, edge timestamps."""
import pytest

from protocol_emulator.v3 import model


def test_signed_distance_is_positive_just_before_the_deadline() -> None:
    assert model.signed_distance(due=100, now=90) == 10


def test_signed_distance_is_negative_just_after_the_deadline() -> None:
    assert model.signed_distance(due=90, now=100) == -10


def test_signed_distance_handles_wraparound() -> None:
    # due is 10 ticks after now, across the 24-bit wrap.
    assert model.signed_distance(due=5, now=0xFFFFFB) == 10


def test_signed_distance_saturates_at_the_window_edges() -> None:
    assert model.signed_distance(due=0x7FFFFF, now=0) == 0x7FFFFF
    assert model.signed_distance(due=0x800000, now=0) == -0x800000


def test_a_scheduled_change_does_not_apply_early() -> None:
    cell = model.PinCell()
    cell.schedule(due=10, value=1, oe=1, now=0)
    for now in range(0, 10):
        cell.tick(now)
        assert cell.value == 0
    cell.tick(10)
    assert cell.value == 1


def test_the_slot_frees_when_it_fires() -> None:
    cell = model.PinCell()
    cell.schedule(due=3, value=1, oe=1, now=0)
    assert cell.pending is not None
    cell.tick(3)
    assert cell.pending is None


def test_a_past_deadline_applies_immediately_and_sets_missed() -> None:
    cell = model.PinCell()
    cell.schedule(due=5, value=1, oe=1, now=9)
    assert cell.value == 1
    assert cell.missed is True
    assert cell.pending is None


def test_an_on_time_commit_does_not_set_missed() -> None:
    cell = model.PinCell()
    cell.schedule(due=5, value=1, oe=1, now=5)
    assert cell.value == 1
    assert cell.missed is False


def test_writing_an_occupied_slot_is_an_error() -> None:
    cell = model.PinCell()
    cell.schedule(due=10, value=1, oe=1, now=0)
    with pytest.raises(model.SlotBusy):
        cell.schedule(due=11, value=0, oe=1, now=0)


def test_missed_is_sticky_until_cleared() -> None:
    cell = model.PinCell()
    cell.schedule(due=0, value=1, oe=1, now=5)
    cell.tick(6)
    cell.tick(7)
    assert cell.missed is True
    cell.clear_missed()
    assert cell.missed is False


def test_open_drain_never_drives_high() -> None:
    cell = model.PinCell(open_drain=True)
    cell.schedule(due=0, value=1, oe=1, now=0)
    assert cell.drive() == (0, 0), "a requested high must become high impedance"


def test_open_drain_still_drives_low() -> None:
    cell = model.PinCell(open_drain=True)
    cell.schedule(due=0, value=0, oe=1, now=0)
    assert cell.drive() == (0, 1)


def test_an_input_edge_is_timestamped_with_its_polarity() -> None:
    cell = model.PinCell()
    cell.observe(level=0, now=0)
    cell.observe(level=1, now=17)
    assert cell.last_edge == 17
    assert cell.last_polarity == 1
    cell.observe(level=0, now=25)
    assert cell.last_edge == 25
    assert cell.last_polarity == 0


def test_a_steady_input_does_not_move_the_timestamp() -> None:
    cell = model.PinCell()
    cell.observe(level=1, now=4)
    cell.observe(level=1, now=5)
    assert cell.last_edge == 4
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_model_pin_cell.py -v
```

Expected: `ModuleNotFoundError: No module named 'protocol_emulator.v3.model'`.

- [ ] **Step 3: Write the timebase and pin cell**

Start `host/protocol_emulator/v3/model.py`:

```python
"""Cycle-accurate model of the V3 design.

This model is the reference the RTL is compared against on every clock edge.
It is written to be read: where a choice exists between a fast implementation
and one that mirrors the hardware structure, it mirrors the hardware.
"""
from typing import NamedTuple

from . import PIN_COUNT, PROGRAM_WORDS, TIMEBASE_BITS

TIMEBASE_MASK = (1 << TIMEBASE_BITS) - 1
TIMEBASE_HALF = 1 << (TIMEBASE_BITS - 1)


class SlotBusy(RuntimeError):
    """A commit targeted a pin whose pending slot is still occupied.

    In hardware this is not an error: the engine stalls. The model raises so
    that callers which have not checked :meth:`PinCell.free` first are caught.
    """


def signed_distance(due: int, now: int) -> int:
    """Signed 24-bit distance from ``now`` to ``due``.

    Positive means the deadline is in the future, zero means it is this cycle,
    negative means it has already passed. The comparison is modular, so it is
    correct across the 349.5 ms counter wrap as long as the true distance stays
    inside +/-8,388,608 ticks.
    """
    return ((due - now + TIMEBASE_HALF) & TIMEBASE_MASK) - TIMEBASE_HALF


class Pending(NamedTuple):
    """The single pending commit held by a pin cell."""

    due: int
    value: int
    oe: int


class PinCell:
    """One protocol pin: output scheduling and input edge timestamping.

    The cell holds at most one pending commit. The owning engine stalls rather
    than overwriting an occupied slot, which throttles the engine to the pin's
    event rate without a separate synchronization instruction.
    """

    def __init__(self, open_drain: bool = False) -> None:
        self.open_drain = open_drain
        self.value = 0
        self.oe = 0
        self.pending: Pending | None = None
        self.missed = False
        self.last_edge = 0
        self.last_polarity = 0
        self._level = 0

    @property
    def free(self) -> bool:
        """Whether a new commit can be accepted this cycle."""
        return self.pending is None

    def schedule(self, due: int, value: int, oe: int, now: int) -> None:
        """Schedule ``(value, oe)`` to apply at ``due``.

        A deadline already in the past applies immediately and sets the sticky
        missed flag; the engine is not stalled and the edge is not dropped.
        """
        if self.pending is not None:
            raise SlotBusy("pin cell slot is occupied")
        if signed_distance(due, now) <= 0:
            if signed_distance(due, now) < 0:
                self.missed = True
            self._apply(value, oe)
            return
        self.pending = Pending(due & TIMEBASE_MASK, value, oe)

    def tick(self, now: int) -> None:
        """Advance one cycle, applying the pending commit if it is due."""
        if self.pending is not None and signed_distance(self.pending.due, now) <= 0:
            self._apply(self.pending.value, self.pending.oe)
            self.pending = None

    def _apply(self, value: int, oe: int) -> None:
        self.value = value & 1
        self.oe = oe & 1

    def drive(self) -> tuple[int, int]:
        """Return the ``(value, oe)`` actually presented to the pad.

        An open-drain pin asked to drive high is released instead, so the
        external pull-up defines the high level and no bus contention is
        possible.
        """
        if self.open_drain and self.value:
            return (0, 0)
        return (self.value, self.oe)

    def observe(self, level: int, now: int) -> None:
        """Record a synchronized input level, timestamping any edge."""
        level &= 1
        if level != self._level:
            self.last_edge = now & TIMEBASE_MASK
            self.last_polarity = level
        self._level = level

    @property
    def level(self) -> int:
        """The last observed synchronized input level."""
        return self._level

    def clear_missed(self) -> None:
        """Clear the sticky missed-deadline flag."""
        self.missed = False


class Timebase:
    """The shared free-running counter and its epoch extension."""

    def __init__(self) -> None:
        self.now = 0
        self.epoch = 0

    def tick(self) -> None:
        """Advance one clock cycle, carrying into the epoch on wrap."""
        self.now = (self.now + 1) & TIMEBASE_MASK
        if self.now == 0:
            self.epoch = (self.epoch + 1) & 0xFF

    @property
    def wide(self) -> int:
        """The 32-bit timestamp used by capture and host readout."""
        return (self.epoch << TIMEBASE_BITS) | self.now
```

- [ ] **Step 4: Run and confirm green**

```bash
python -m pytest tests/v3/test_model_pin_cell.py -v
python -m mypy --config-file mypy.ini
```

Expected: 14 passed, mypy clean.

- [ ] **Step 5: Add a timebase wrap test**

Append to `tests/v3/test_model_pin_cell.py`:

```python
def test_the_timebase_wraps_into_the_epoch() -> None:
    base = model.Timebase()
    base.now = model.TIMEBASE_MASK
    base.tick()
    assert base.now == 0
    assert base.epoch == 1
    assert base.wide == 1 << 24


def test_a_deadline_across_the_wrap_still_fires_on_time() -> None:
    cell = model.PinCell()
    base = model.Timebase()
    base.now = model.TIMEBASE_MASK - 4
    due = (base.now + 10) & model.TIMEBASE_MASK
    cell.schedule(due=due, value=1, oe=1, now=base.now)
    fired_at = None
    for _ in range(20):
        base.tick()
        cell.tick(base.now)
        if cell.value and fired_at is None:
            fired_at = base.now
    assert fired_at == due
    assert cell.missed is False
```

- [ ] **Step 6: Run again**

```bash
python -m pytest tests/v3/test_model_pin_cell.py -v
```

Expected: 16 passed.

- [ ] **Step 7: Commit**

```bash
git add host/protocol_emulator/v3/model.py tests/v3/test_model_pin_cell.py
git commit -m "Add the timebase and pin cell model"
```

---

### Task 9: Engine model — fetch pipeline and cursor

**Files:**
- Modify: `host/protocol_emulator/v3/model.py` (append)
- Test: `tests/v3/test_model_engine.py`

**Interfaces:**
- Consumes: `PinCell`, `Timebase`, `signed_distance`, `isa.decode`.
- Produces:
  - `class ProgramMemory` with `write(addr: int, word: int) -> None`, `valid: bool`,
    `read(addr: int) -> int`, `reset() -> None`.
  - `class Engine(pins: list[PinCell], owner: int, open_drain: int = 0)` with methods
    `load(image: Image) -> None`, `start(now: int) -> None`, `stop() -> None`,
    `step(now: int) -> None`, and attributes `pins: list[PinCell]`, `memory: ProgramMemory`,
    `owner: int`, `pc: int`, `cursor: int`, `regs: list[int]` (four entries), `isr: int`,
    `running: bool`, `halted: bool`, `stalled: bool`, `errors: int`, `fault_code: int`,
    `retired: int` (instructions retired since START), `cycles_to_halt: int` (cycles elapsed
    since START, frozen at halt).
  - `class NotLoaded(RuntimeError)` — raised by `start()` when the image is incomplete.
  - Error bit constants `E_ILLEGAL`, `E_INVALID_WORD`, `E_STACK`, `E_OWNERSHIP`, `E_FIRMWARE`,
    `E_QUEUE`, `E_TIMEOUT`.

The fetch contract, stated once here and enforced in both model and RTL: the engine presents an
address on cycle N and the word arrives on N+1. PC+1 is prefetched every cycle, so straight-line
code retires one instruction per cycle. A taken branch discards the prefetched word, presents the
target on the branch cycle, and retires the target on the cycle after that: exactly two cycles,
always, with no fall-through fast path.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_model_engine.py`:

```python
"""Engine semantics: the fetch contract, the cursor, stalls and faults."""
import pytest

from protocol_emulator.v3 import isa, model


def run(source: str, cycles: int = 200) -> model.Engine:
    """Assemble, load, start, and run an engine for a fixed number of cycles."""
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble(source))
    base = model.Timebase()
    engine.start(base.now)
    for _ in range(cycles):
        engine.step(base.now)
        for cell in engine.pins:
            cell.tick(base.now)
        base.tick()
    return engine


def test_straight_line_code_retires_one_instruction_per_cycle() -> None:
    engine = run("LI reg=0 value=1\nLI reg=1 value=2\nLI reg=2 value=3\nHALT\n", cycles=6)
    assert engine.regs[0] == 1 and engine.regs[1] == 2 and engine.regs[2] == 3
    assert engine.retired == 4, "three loads plus the halt, one per cycle"


def test_a_taken_branch_costs_exactly_two_cycles() -> None:
    engine = run("JMP target=2\nFAIL code=1\nHALT\n", cycles=4)
    assert engine.halted
    assert engine.errors == 0
    assert engine.cycles_to_halt == 3, "one cycle for JMP, one bubble, one for HALT"


def test_an_untaken_branch_costs_one_cycle() -> None:
    source = "BRC target=3 condition=timeout\nLI reg=0 value=7\nHALT\nFAIL code=1\n"
    engine = run(source, cycles=6)
    assert engine.regs[0] == 7
    assert engine.cycles_to_halt == 3


def test_advance_moves_the_cursor_without_stalling() -> None:
    engine = run("ADVANCE ticks=1000\nHALT\n", cycles=3)
    assert engine.cursor == 1000
    assert engine.cycles_to_halt == 2, "ADVANCE never stalls"


def test_the_cursor_starts_at_now() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("HALT\n"))
    engine.start(now=12345)
    assert engine.cursor == 12345


def test_a_commit_schedules_at_cursor_plus_offset() -> None:
    engine = run("ADVANCE ticks=100\nOUT pin=0 level=1 offset=5\nHALT\n", cycles=4)
    assert engine.pins[0].pending == model.Pending(due=105, value=1, oe=1)


def test_a_commit_to_an_occupied_slot_stalls_the_engine() -> None:
    source = (
        "ADVANCE ticks=500\n"
        "OUT pin=0 level=1 offset=0\n"
        "OUT pin=0 level=0 offset=1\n"
        "HALT\n"
    )
    engine = run(source, cycles=10)
    # The second commit cannot be accepted while the first is pending, so the
    # engine is still stalled ten cycles in, 490 ticks short of the deadline.
    assert engine.stalled
    assert not engine.halted


def test_a_commit_to_an_unowned_pin_is_a_fault() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0x01)
    engine.load(isa.assemble("OUT pin=3 level=1 offset=0\nHALT\n"))
    engine.start(0)
    engine.step(0)
    assert engine.errors & model.E_OWNERSHIP


def test_an_unwritten_word_faults_instead_of_executing() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.memory.write(0, isa.encode("JMP", target=200))
    with pytest.raises(model.NotLoaded):
        engine.start(0)


def test_start_is_refused_until_every_word_is_written() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    assert engine.memory.valid is False
    engine.load(isa.assemble("HALT\n"))
    assert engine.memory.valid is True


def test_call_and_ret_nest_twice() -> None:
    source = (
        "CALL target=4\n"
        "HALT\n"
        "LI reg=2 value=3\n"
        "RET\n"
        "LI reg=0 value=1\n"
        "CALL target=2\n"
        "LI reg=1 value=2\n"
        "RET\n"
    )
    engine = run(source, cycles=30)
    assert (engine.regs[0], engine.regs[1], engine.regs[2]) == (1, 2, 3)
    assert engine.halted and engine.errors == 0


def test_a_third_nested_call_faults() -> None:
    source = "CALL target=2\nHALT\nCALL target=4\nRET\nCALL target=6\nRET\nRET\n"
    engine = run(source, cycles=30)
    assert engine.errors & model.E_STACK


def test_ret_with_an_empty_stack_faults() -> None:
    engine = run("RET\n", cycles=5)
    assert engine.errors & model.E_STACK


def test_djnz_loops_the_expected_number_of_times() -> None:
    source = "LI reg=0 value=3\nloop:\nLI reg=1 value=9\nDJNZ reg=0 target=loop\nHALT\n"
    engine = run(source, cycles=40)
    assert engine.regs[0] == 0
    assert engine.halted


def test_fail_sets_a_firmware_error_and_halts() -> None:
    engine = run("FAIL code=42\n", cycles=5)
    assert engine.errors & model.E_FIRMWARE
    assert engine.fault_code == 42
    assert engine.halted


def test_an_illegal_word_faults() -> None:
    engine = model.Engine(pins=[model.PinCell() for _ in range(8)], owner=0xFF)
    engine.load(isa.assemble("HALT\n"))
    engine.memory.write(0, 0xFFFFFFFF)
    engine.start(0)
    engine.step(0)
    engine.step(1)
    assert engine.errors & model.E_ILLEGAL
```

- [ ] **Step 2: Run it and confirm it fails**

```bash
python -m pytest tests/v3/test_model_engine.py -v
```

Expected: `AttributeError: module 'protocol_emulator.v3.model' has no attribute 'Engine'`.

- [ ] **Step 3: Append `ProgramMemory` and `Engine` to `model.py`**

Write the engine so that each `step()` performs, in order: resolve stall conditions; if not
stalled, execute the word delivered by the previous cycle's fetch; compute the next fetch address;
latch the fetched word for the next cycle. Keep the fetch register explicit — the RTL has the same
register and the differential test compares against it.

Key structures:

```python
#: Error bits, mirrored by `E_*` localparams in src/v3_engine.v.
E_ILLEGAL = 0x0001
E_INVALID_WORD = 0x0002
E_STACK = 0x0004
E_OWNERSHIP = 0x0008
E_FIRMWARE = 0x0010
E_QUEUE = 0x0020
E_TIMEOUT = 0x0040


class NotLoaded(RuntimeError):
    """START was attempted before every program word was written."""


class ProgramMemory:
    """256x32 program store with a registered read and a validity bitmap.

    SRAM contents are undefined at power-up, so a word that has never been
    written must not be executed. The bitmap makes that a detectable fault
    rather than undefined behaviour, and START is refused until it is full.
    """

    def __init__(self) -> None:
        self._words = [0] * PROGRAM_WORDS
        self._written = [False] * PROGRAM_WORDS

    def write(self, addr: int, word: int) -> None:
        if not 0 <= addr < PROGRAM_WORDS:
            raise ValueError(f"address {addr} is outside 0..{PROGRAM_WORDS - 1}")
        self._words[addr] = word & 0xFFFFFFFF
        self._written[addr] = True

    def read(self, addr: int) -> int:
        return self._words[addr & (PROGRAM_WORDS - 1)]

    def is_written(self, addr: int) -> bool:
        return self._written[addr & (PROGRAM_WORDS - 1)]

    @property
    def valid(self) -> bool:
        """Whether every word has been written. START requires this."""
        return all(self._written)

    def reset(self) -> None:
        """Deselection clears the bitmap; the contents are no longer trusted."""
        self._written = [False] * PROGRAM_WORDS
```

The `Engine` class is long. Implement it incrementally: get the tests passing in the order they are
written in Step 1, committing nothing until all pass. Required attributes and behaviour:

- `__init__(self, pins: list[PinCell], owner: int, open_drain: int = 0)` — stores the shared pin
  list and the ownership mask.
- `load(image)` — writes all 256 words through `self.memory.write`.
- `start(now)` — raises `NotLoaded` if `not self.memory.valid`; sets `cursor = now`, `pc = 0`,
  `running = True`, clears `retired`, `cycles_to_halt`, and the fetch register, then performs the
  first fetch so cycle 0 has a word.
- `step(now)` — one clock cycle. Order inside the method:
  1. If not `running`, return.
  2. `self.cycles_to_halt += 1`.
  3. Evaluate the current stall condition (`WAITTIME`, `WAITEDGE`, `WAITFOR`, `WAITEVENT`,
     `PULL`, or a commit whose target pins are not all free). If stalled, return without
     advancing `pc` or the fetch register.
  4. Decode `self._fetched`. On `IllegalInstruction`, set `E_ILLEGAL` and halt.
  5. If `not self.memory.is_written(self._fetch_addr)`, set `E_INVALID_WORD` and halt.
  6. Execute. `self.retired += 1`.
  7. If the instruction redirected the fetch, set `self._bubble = True` and fetch the target;
     otherwise fetch `pc + 1`.
- Commits must check `self.owner >> pin & 1` and set `E_OWNERSHIP` if the pin is not owned.
- `SET`, `OE`, `DRIVE` apply to every pin in their mask; the engine stalls until every one of them
  is free, then schedules all of them in the same cycle.

- [ ] **Step 4: Run the tests until green**

```bash
python -m pytest tests/v3/test_model_engine.py -v
```

Expected: 16 passed. Fix the model, not the tests. If a test looks wrong, re-read the spec section
3 before changing it, and if the spec is genuinely wrong, fix the spec in the same commit.

- [ ] **Step 5: Type check**

```bash
python -m mypy --config-file mypy.ini
```

Expected: clean.

- [ ] **Step 6: Commit**

```bash
git add host/protocol_emulator/v3/model.py tests/v3/test_model_engine.py
git commit -m "Add the engine model: fetch pipeline, cursor, stalls and faults"
```

---

### Task 10: Queues, CRC and capture in the model

**Files:**
- Modify: `host/protocol_emulator/v3/model.py` (append)
- Test: `tests/v3/test_model_crc.py`, `tests/v3/test_model_queues.py`

**Interfaces:**
- Produces:
  - `class Crc` with `configure(poly: int, msb_first: bool) -> None`, `load(seed: int) -> None`,
    `shift(bit: int) -> None`, `value: int`.
  - `class Queues` with `push_tx(data: bytes) -> None`, `pull() -> int | None`,
    `push_rx(value: int, meta: int) -> None`, `pop_rx() -> tuple[int, int] | None`,
    `tx_level: int`, `rx_level: int`. Both are 16 deep.
  - `class Capture` with `record(...)`, 32 entries, wraparound behaviour matching
    `src/v2_capture.v`.

- [ ] **Step 1: Write the failing CRC test**

`tests/v3/test_model_crc.py`:

```python
"""The CRC unit matches published vectors for the polynomials V3 must support."""
from protocol_emulator.v3 import model


def crc_over(data: bytes, poly: int, seed: int, msb_first: bool) -> int:
    unit = model.Crc()
    unit.configure(poly=poly, msb_first=msb_first)
    unit.load(seed)
    for byte in data:
        order = range(7, -1, -1) if msb_first else range(8)
        for index in order:
            unit.shift((byte >> index) & 1)
    return unit.value


def test_crc16_ccitt_false_matches_the_check_vector() -> None:
    # CRC-16/CCITT-FALSE: poly 0x1021, init 0xFFFF, MSB first, check "123456789".
    assert crc_over(b"123456789", poly=0x1021, seed=0xFFFF, msb_first=True) == 0x29B1


def test_crc16_arc_matches_the_check_vector() -> None:
    # CRC-16/ARC: reflected poly 0xA001, init 0x0000, LSB first.
    assert crc_over(b"123456789", poly=0xA001, seed=0x0000, msb_first=False) == 0xBB3D


def test_usb_crc5_stays_in_five_bits_and_has_a_constant_residual() -> None:
    """CRC5 for low-speed USB, which V3 is wired for but does not yet implement.

    No published check value is asserted here, because the one in circulation
    for a given token is easy to get wrong and a wrong constant is worse than
    no constant. Instead this checks the two properties that actually matter:
    a 5-bit polynomial keeps the register inside five bits, and feeding a
    message followed by its own CRC produces the same residual every time.
    That residual is captured from the first message and compared against the
    others, so the test has no external magic number.
    """

    def crc5(bits: list[int], seed: int = 0x1F) -> int:
        unit = model.Crc()
        unit.configure(poly=0x0014, msb_first=False)  # x^5+x^2+1, reflected
        unit.load(seed)
        for bit in bits:
            unit.shift(bit)
        assert unit.value <= 0x1F, "a 5-bit polynomial must not leave five bits"
        return unit.value

    residuals = set()
    for token in (0x0015, 0x0000, 0x07FF, 0x03A5):
        message = [(token >> index) & 1 for index in range(11)]
        check = crc5(message) ^ 0x1F
        appended = message + [(check >> index) & 1 for index in range(5)]
        residuals.add(crc5(appended))
    assert len(residuals) == 1, f"residual is not constant: {residuals}"


def test_configuring_does_not_disturb_the_state() -> None:
    unit = model.Crc()
    unit.configure(poly=0x1021, msb_first=True)
    unit.load(0xFFFF)
    unit.shift(1)
    before = unit.value
    unit.configure(poly=0x1021, msb_first=True)
    assert unit.value == before


def test_the_state_stays_sixteen_bits() -> None:
    unit = model.Crc()
    unit.configure(poly=0x8005, msb_first=True)
    unit.load(0xFFFF)
    for _ in range(64):
        unit.shift(1)
        assert 0 <= unit.value <= 0xFFFF
```

- [ ] **Step 2: Run and confirm it fails**

```bash
python -m pytest tests/v3/test_model_crc.py -v
```

Expected: `AttributeError: module ... has no attribute 'Crc'`.

- [ ] **Step 3: Implement `Crc`**

```python
class Crc:
    """A shared 16-bit CRC/LFSR with a configurable polynomial and direction.

    One bit is shifted per ``CRCBIT`` instruction. Bit-serial rather than
    byte-wide because low-speed USB, which V3 is wired for even though it is
    out of scope for the submission, needs a 5-bit CRC over an 11-bit field
    that no byte-wide unit handles cleanly. The cost is one instruction per
    bit, paid only in the burst at the end of a frame.

    ``msb_first`` selects the shift direction, which also selects where the
    polynomial taps sit:

    - MSB first shifts left and XORs the normal-form polynomial when bit 15
      shifts out. Use for CRC-16/CCITT with poly 0x1021.
    - LSB first shifts right and XORs the reflected polynomial when bit 0
      shifts out. Use for CRC-16/ARC with poly 0xA001 and for USB CRC5.
    """

    def __init__(self) -> None:
        self._poly = 0
        self._msb_first = True
        self._state = 0

    def configure(self, poly: int, msb_first: bool) -> None:
        """Set the polynomial and direction. The state is untouched."""
        self._poly = poly & 0xFFFF
        self._msb_first = msb_first

    def load(self, seed: int) -> None:
        """Load the seed, typically all ones or all zeros."""
        self._state = seed & 0xFFFF

    def shift(self, bit: int) -> None:
        """Shift one message bit through the register."""
        if self._msb_first:
            feedback = ((self._state >> 15) ^ (bit & 1)) & 1
            self._state = (self._state << 1) & 0xFFFF
        else:
            feedback = (self._state ^ (bit & 1)) & 1
            self._state >>= 1
        if feedback:
            self._state ^= self._poly

    @property
    def value(self) -> int:
        """The current 16-bit register contents."""
        return self._state
```

- [ ] **Step 4: Run and confirm green**

```bash
python -m pytest tests/v3/test_model_crc.py -v
```

Expected: 5 passed. If the CRC5 vector fails, check the seed and final XOR convention before
changing the shift logic — USB CRC5 is seeded all-ones and the residual is inverted.

- [ ] **Step 5: Write and pass the queue tests**

`tests/v3/test_model_queues.py`: cover push to full (17th byte rejected, `tx_level` stays 16),
pull from empty returns `None` without disturbing the register, FIFO ordering across a wraparound
of the 16-entry ring, `rx_level` saturation, and that a reset empties both.

Implement `Queues` to match, then:

```bash
python -m pytest tests/v3/test_model_queues.py -v
```

- [ ] **Step 6: Port capture from V2**

Reimplement the V2 capture semantics (32 records, timestamp, wraparound, trigger) against the
shared 32-bit timestamp. Reference the retired `src/v2_capture.v` through
`git show v2:src/v2_capture.v` rather than from memory.

- [ ] **Step 7: Commit**

```bash
git add host/protocol_emulator/v3/model.py tests/v3/test_model_crc.py tests/v3/test_model_queues.py
git commit -m "Add CRC, queues and capture to the model"
```

---

### Task 11: Host frame codec

**Files:**
- Create: `host/protocol_emulator/v3/host.py`
- Test: `tests/v3/test_host_frame.py`

**Interfaces:**
- Produces:
  - `FRAME_BITS: int = 48`.
  - `class Frame(NamedTuple)` with `cmd: int`, `addr: int`, `payload: int`.
  - `pack(cmd: int, addr: int, payload: int) -> int` — a 48-bit integer, MSB first on the wire.
  - `unpack(bits: int) -> Frame`.
  - `COMMANDS: dict[str, int]` and `REJECT_BIT: int = 0x80`, `ENGINE_BIT: int = 0x40`.

- [ ] **Step 1: Write the failing test**

`tests/v3/test_host_frame.py`:

```python
"""The host frame is exactly 48 bits, cmd/addr/payload, MSB first."""
import pytest

from protocol_emulator.v3 import host


def test_a_frame_is_48_bits() -> None:
    assert host.FRAME_BITS == 48


def test_fields_pack_in_wire_order() -> None:
    bits = host.pack(cmd=0x12, addr=0x34, payload=0xDEADBEEF)
    assert bits >> 40 == 0x12
    assert (bits >> 32) & 0xFF == 0x34
    assert bits & 0xFFFFFFFF == 0xDEADBEEF


def test_pack_and_unpack_round_trip() -> None:
    frame = host.Frame(cmd=0x7F, addr=0xFF, payload=0x01234567)
    assert host.unpack(host.pack(*frame)) == frame


def test_the_reject_bit_and_the_engine_bit_do_not_collide() -> None:
    assert host.REJECT_BIT != host.ENGINE_BIT
    assert host.REJECT_BIT == 0x80, "bit 7 of the reply command byte flags rejection"


def test_no_command_sets_the_reject_bit() -> None:
    for name, code in host.COMMANDS.items():
        assert not code & host.REJECT_BIT, f"{name} collides with the reject flag"


def test_no_command_sets_the_engine_bit() -> None:
    for name, code in host.COMMANDS.items():
        assert not code & host.ENGINE_BIT, f"{name} collides with the engine select bit"


def test_an_oversized_field_is_rejected() -> None:
    with pytest.raises(ValueError, match="payload"):
        host.pack(cmd=0, addr=0, payload=1 << 32)
```

- [ ] **Step 2: Run, fail, implement, pass, commit**

```bash
python -m pytest tests/v3/test_host_frame.py -v
python -m mypy --config-file mypy.ini
git add host/protocol_emulator/v3/host.py tests/v3/test_host_frame.py
git commit -m "Add the 48-bit host frame codec"
```

---

## Phase 3 — RTL

Every module in this phase follows the same shape: write the Verilog, write a formal script that
proves its core properties by temporal induction, write a cocotb test that checks it against the
model, and commit only when all three pass.

**Icarus and Yosys are not installed on Windows.** This phase runs from WSL. Complete Task 26
before starting Task 12, or run these tasks on a machine that already has the tools.

### Task 12: Timebase RTL

**Files:**
- Create: `src/v3_timebase.v`
- Create: `formal/v3_timebase.ys`
- Test: `test/v3/test_timebase.py`, `test/Makefile.v3timebase`

**Interfaces:**
- Produces: module `v3_timebase` with ports
  `input wire clk, input wire rst_n, output reg [23:0] now, output reg [7:0] epoch`.

- [ ] **Step 1: Write the module**

`src/v3_timebase.v`:

```verilog
`default_nettype none

// The single time reference. Everything that schedules, waits, timestamps or
// captures reads this counter; nothing else keeps its own notion of time.
//
// At 48 MHz the 24-bit counter wraps every 349.5 ms. Deadline comparisons use
// a signed distance so the wrap is invisible to firmware as long as a deadline
// stays inside +/-174.8 ms of the present. The epoch extends the timestamp to
// 32 bits for capture and host readout, covering about 89 seconds.
module v3_timebase (
    input  wire        clk,
    input  wire        rst_n,
    output reg  [23:0] now,
    output reg  [7:0]  epoch
);

    always @(posedge clk) begin
        if (!rst_n) begin
            now   <= 24'd0;
            epoch <= 8'd0;
        end else begin
            now <= now + 24'd1;
            if (&now) epoch <= epoch + 8'd1;
        end
    end

`ifdef FORMAL
    reg started = 1'b0;
    always @(posedge clk) started <= 1'b1;

    always @(posedge clk) if (started) begin
        // The counter only ever advances by one.
        assert(now == $past(now) + 24'd1 || !$past(rst_n));
        // The epoch advances exactly when the counter wraps.
        if ($past(rst_n) && $past(&now)) assert(epoch == $past(epoch) + 8'd1);
        if ($past(rst_n) && !$past(&now)) assert(epoch == $past(epoch));
    end
`endif

endmodule

`default_nettype wire
```

- [ ] **Step 2: Write the formal script**

`formal/v3_timebase.ys`:

```tcl
# Proof: F-V3-TIMEBASE
# Top: v3_timebase
# Assumptions: initial sampled reset.
# Properties: monotonic single-step advance; epoch carries exactly on wrap.
# Method: temporal induction.
# Scope and abstractions: formal/README.md.

read_verilog -formal src/v3_timebase.v
prep -top v3_timebase
flatten
opt
async2sync
dffunmap
sat -seq 4 -tempinduct -maxsteps 16 -set-assumes -prove-asserts -verify
```

- [ ] **Step 3: Run the proof**

```bash
yosys -q formal/v3_timebase.ys
```

Expected: `SAT temporal induction proof finished - no model found: SUCCESS` and exit 0.

- [ ] **Step 4: Add the cocotb test**

`test/Makefile.v3timebase`:

```makefile
SIM ?= icarus
TOPLEVEL_LANG = verilog
VERILOG_SOURCES = $(CURDIR)/../src/v3_timebase.v
TOPLEVEL = v3_timebase
COCOTB_TEST_MODULES = v3.test_timebase
SIM_BUILD = sim_build/v3timebase
COCOTB_RESULTS_FILE = results_v3timebase.xml
export PYTHONPATH := $(CURDIR):$(CURDIR)/../host:$(PYTHONPATH)
include $(shell cocotb-config --makefiles)/Makefile.sim
```

`test/v3/test_timebase.py`:

```python
"""The RTL timebase matches the model cycle for cycle, including the wrap."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge

from protocol_emulator.v3 import model


@cocotb.test()
async def test_v3_timebase_differential(dut):
    """Run 2000 cycles from reset and compare against the model every edge."""
    cocotb.start_soon(Clock(dut.clk, 20.833, unit="ns").start())
    dut.rst_n.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)

    base = model.Timebase()
    for cycle in range(2000):
        await RisingEdge(dut.clk)
        base.tick()
        assert int(dut.now.value) == base.now, (
            f"cycle {cycle}: RTL now={int(dut.now.value)} model now={base.now}"
        )
        assert int(dut.epoch.value) == base.epoch
```

- [ ] **Step 5: Run it**

```bash
make -C test -f Makefile.v3timebase
```

Expected: `** TESTS=1 PASS=1 FAIL=0 SKIP=0 **`.

If the first compared cycle is off by one, the reset release and the model's starting value are
misaligned — fix the testbench alignment, not the RTL, and record which cycle the comparison starts
on in a comment.

- [ ] **Step 6: Commit**

```bash
git add src/v3_timebase.v formal/v3_timebase.ys test/Makefile.v3timebase test/v3/test_timebase.py
git commit -m "Add the shared timebase with a formal proof and differential test"
```

---

### Task 13: Pin cell RTL

This is the module the whole architecture rests on. Its proof is the most important one in the
project.

**Files:**
- Create: `src/v3_pin_cell.v`
- Create: `formal/v3_pin_cell.ys`
- Test: `test/v3/test_pin_cell.py`, `test/Makefile.v3pincell`

**Interfaces:**
- Produces: module `v3_pin_cell` with ports:

```verilog
input  wire        clk,
input  wire        rst_n,
input  wire        enable,        // low releases the pin and clears the slot
input  wire [23:0] now,
input  wire        open_drain,
input  wire        commit,        // one-cycle strobe from the owning engine
input  wire [23:0] commit_due,
input  wire        commit_value,
input  wire        commit_oe,
input  wire        clear_missed,
input  wire        pin_in,        // raw pad input
output wire        slot_free,     // engine stalls on commit when this is low
output wire        pin_out,
output wire        pin_oe,
output reg         missed,
output reg  [23:0] edge_time,
output reg         edge_polarity,
output wire        level          // synchronized, filtered input level
```

- [ ] **Step 1: Write the module**

Implementation notes that must hold, because the proof and the mutation gate both check them:

- `distance = commit_due - now` evaluated as `$signed(commit_due - now)`. The compare is
  `distance <= 0`, never an unsigned compare.
- On `commit` with `distance > 0` and `slot_free`: latch the slot.
- On `commit` with `distance <= 0`: apply immediately. Set `missed` only when `distance < 0`,
  not when it is zero.
- Every cycle: if the slot is occupied and its distance has reached zero or gone negative, apply
  and free. A slot that is occupied cannot be overwritten; `slot_free` is the engine's interlock.
- `pin_oe = open_drain ? (value_r ? 1'b0 : oe_r) : oe_r` and
  `pin_out = open_drain ? 1'b0 : value_r`.
- `!enable` or `!rst_n` clears `oe_r`, the slot, and `missed`.

- [ ] **Step 2: Write the formal script with the full property set**

`formal/v3_pin_cell.ys` proves, by temporal induction:

| Property | Assertion |
|---|---|
| Never early | If the slot is occupied and `$signed(due - now) > 0`, the output did not change this cycle. |
| Fires on time | If the slot was occupied last cycle with `$past(due) == now`, the output now equals the slot value. |
| Past-due applies immediately | On `commit` with `$signed(due - now) < 0`, `pin_out` equals `commit_value` combinationally within the cycle, and `missed` is set on the next edge. |
| Exactly-due does not set missed | On `commit` with `due == now`, `missed` is unchanged. |
| No overwrite | If the slot is occupied, `slot_free` is low, and the slot contents are stable unless it fires. |
| Open drain | `open_drain` implies `!(pin_out && pin_oe)`. |
| Release | `!enable` or `!rst_n` implies `!pin_oe`. |
| Missed is sticky | `missed` only clears on `clear_missed` or reset. |
| Bounded liveness | A slot occupied with distance `d` is free within `d + 1` cycles. Encoded as a counter that asserts `counter <= d + 1`. |

- [ ] **Step 3: Run the proof**

```bash
yosys -q formal/v3_pin_cell.ys
```

Expected: SUCCESS. A failure here means the architecture is wrong, not the proof — read the
counterexample trace before touching the assertions.

- [ ] **Step 4: Write the differential test**

`test/v3/test_pin_cell.py` drives randomized commits and input transitions for 20,000 cycles and
compares `pin_out`, `pin_oe`, `missed`, `edge_time` and `edge_polarity` against `model.PinCell`
every edge. Seed from `PE_SEED` in the environment, default 0, and print the seed on failure.

Include these directed cases explicitly, each as its own `@cocotb.test()`:

```python
@cocotb.test()
async def test_v3_pin_cell_does_not_fire_early(dut): ...


@cocotb.test()
async def test_v3_pin_cell_fires_on_the_due_tick(dut): ...


@cocotb.test()
async def test_v3_pin_cell_past_due_applies_and_flags(dut): ...


@cocotb.test()
async def test_v3_pin_cell_exactly_due_does_not_flag(dut): ...


@cocotb.test()
async def test_v3_pin_cell_slot_busy_blocks_a_second_commit(dut): ...


@cocotb.test()
async def test_v3_pin_cell_open_drain_never_drives_high(dut): ...


@cocotb.test()
async def test_v3_pin_cell_deadline_across_the_timebase_wrap(dut): ...


@cocotb.test()
async def test_v3_pin_cell_random_differential(dut): ...
```

- [ ] **Step 5: Run**

```bash
make -C test -f Makefile.v3pincell
```

Expected: `TESTS=8 PASS=8 FAIL=0`.

- [ ] **Step 6: Commit**

```bash
git add src/v3_pin_cell.v formal/v3_pin_cell.ys test/Makefile.v3pincell test/v3/test_pin_cell.py
git commit -m "Add the pin cell with deadline proofs and a randomized differential test"
```

---

### Task 14: Input filter RTL

**Files:**
- Create: `src/v3_input_filter.v` (port of the V2 module, retrieved with
  `git show v2:src/v2_input_filter.v`)
- Create: `formal/v3_input_filter.ys`
- Test: `test/v3/test_input_filter.py`, `test/Makefile.v3filter`

The behaviour is unchanged from V2: three consecutive agreeing synchronized samples qualify a
change. Update the header comment's timing arithmetic from 25 MHz to 48 MHz — at 48 MHz three
samples is 62.5 ns, so the filter rejects glitches shorter than that.

- [ ] **Step 1: Retrieve, port, and update the comment**
- [ ] **Step 2: Port the V2 formal script, renaming the module**
- [ ] **Step 3: Run `yosys -q formal/v3_input_filter.ys`** — expect SUCCESS
- [ ] **Step 4: Port the V2 filter test and run `make -C test -f Makefile.v3filter`**
- [ ] **Step 5: Commit** — `git commit -m "Port the input filter to V3 at 48 MHz"`

---

### Task 15: Program memory wrapper

**Files:**
- Create: `src/v3_progmem.v`
- Create: `src/v3_sram_blackbox.vh`
- Create: `formal/v3_progmem.ys`
- Test: `test/v3/test_progmem.py`, `test/Makefile.v3progmem`

**Interfaces:**
- Produces: module `v3_progmem` with ports:

```verilog
input  wire        clk,
input  wire        rst_n,
input  wire        fetch_en,      // drives A_MEN; low while the engine stalls
input  wire [7:0]  fetch_addr,
output wire [31:0] fetch_data,    // valid the cycle after fetch_addr
output wire        fetch_valid,   // the fetched word was written
input  wire        write_en,
input  wire [7:0]  write_addr,
input  wire [31:0] write_data,
input  wire        invalidate,    // clears the bitmap on deselect
output wire        image_complete // all 256 bits set; START requires this
```

Three compile modes, selected by define:

- `V3_SRAM_BLACKBOX` — a `(* blackbox *)` declaration for synthesis.
- `FUNCTIONAL` — instantiate the PDK behavioral model of
  `RM_IHPSG13_1P_256x32_c2_bm_bist`.
- Neither — an inferred 256x32 register-file model for fast simulation and formal.

Tie-offs, which the proof checks: `A_DLY = 1'b1`; every `A_BIST_*` input tied low; `A_MEN` driven
by `fetch_en | write_en`.

- [ ] **Step 1: Write a test for the read contract first**

`test/v3/test_progmem.py`:

```python
"""The program store honours the one-cycle registered read contract."""
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


async def reset(dut):
    cocotb.start_soon(Clock(dut.clk, 20.833, unit="ns").start())
    dut.rst_n.value = 0
    dut.fetch_en.value = 0
    dut.write_en.value = 0
    dut.invalidate.value = 0
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    await RisingEdge(dut.clk)


async def write(dut, addr, word):
    dut.write_en.value = 1
    dut.write_addr.value = addr
    dut.write_data.value = word
    await RisingEdge(dut.clk)
    dut.write_en.value = 0


@cocotb.test()
async def test_v3_progmem_read_is_one_cycle_late(dut):
    await reset(dut)
    await write(dut, 7, 0xDEADBEEF)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 7
    await RisingEdge(dut.clk)
    # The address was presented on the edge just passed; data is not valid yet.
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0xDEADBEEF


@cocotb.test()
async def test_v3_progmem_back_to_back_reads_stream(dut):
    await reset(dut)
    for addr in range(8):
        await write(dut, addr, 0x1000 + addr)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 0
    await RisingEdge(dut.clk)
    for addr in range(1, 8):
        dut.fetch_addr.value = addr
        await RisingEdge(dut.clk)
        assert int(dut.fetch_data.value) == 0x1000 + addr - 1


@cocotb.test()
async def test_v3_progmem_image_is_incomplete_until_every_word_is_written(dut):
    await reset(dut)
    for addr in range(255):
        await write(dut, addr, 0)
        assert int(dut.image_complete.value) == 0
    await write(dut, 255, 0)
    await RisingEdge(dut.clk)
    assert int(dut.image_complete.value) == 1


@cocotb.test()
async def test_v3_progmem_fetching_an_unwritten_word_is_not_valid(dut):
    await reset(dut)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 42
    await RisingEdge(dut.clk)
    await RisingEdge(dut.clk)
    assert int(dut.fetch_valid.value) == 0


@cocotb.test()
async def test_v3_progmem_invalidate_clears_the_bitmap(dut):
    await reset(dut)
    for addr in range(256):
        await write(dut, addr, 0)
    assert int(dut.image_complete.value) == 1
    dut.invalidate.value = 1
    await RisingEdge(dut.clk)
    dut.invalidate.value = 0
    await RisingEdge(dut.clk)
    assert int(dut.image_complete.value) == 0


@cocotb.test()
async def test_v3_progmem_address_boundaries(dut):
    await reset(dut)
    await write(dut, 0, 0xFFFFFFFF)
    await write(dut, 255, 0x00000001)
    dut.fetch_en.value = 1
    dut.fetch_addr.value = 0
    await RisingEdge(dut.clk)
    dut.fetch_addr.value = 255
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0xFFFFFFFF
    await RisingEdge(dut.clk)
    assert int(dut.fetch_data.value) == 0x00000001
```

- [ ] **Step 2: Run, confirm it fails, implement, run again**

```bash
make -C test -f Makefile.v3progmem
```

Expected after implementation: `TESTS=6 PASS=6 FAIL=0`.

- [ ] **Step 3: Prove the wrapper**

`formal/v3_progmem.ys` proves: data presented at N+1 matches the word written to that address;
`A_MEN` is low when neither `fetch_en` nor `write_en` is asserted; `image_complete` is true if and
only if all 256 bitmap bits are set; a write sets exactly one bitmap bit; `invalidate` clears all
of them.

```bash
yosys -q formal/v3_progmem.ys
```

Expected: SUCCESS.

- [ ] **Step 4: Verify against the real macro model**

Once the PDK is available (Task 26), rerun with the behavioral model linked:

```bash
make -C test -f Makefile.v3progmem \
  EXTRA_ARGS="-DFUNCTIONAL" \
  VERILOG_SOURCES="$PDK_ROOT/ihp-sg13g2/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_256x32_c2_bm_bist.v ../src/v3_progmem.v"
```

Expected: the same 6 tests pass. If they do not, the wrapper's assumptions about the macro's timing
are wrong and the fetch contract in the engine must be revisited before continuing.

- [ ] **Step 5: Commit**

```bash
git add src/v3_progmem.v src/v3_sram_blackbox.vh formal/v3_progmem.ys \
  test/Makefile.v3progmem test/v3/test_progmem.py
git commit -m "Add the SRAM program memory wrapper with a validity bitmap"
```

---

### Task 16: CRC RTL

**Files:**
- Create: `src/v3_crc.v`
- Create: `formal/v3_crc.ys`
- Test: `test/v3/test_crc.py`, `test/Makefile.v3crc`

- [ ] **Step 1: Write the module** mirroring `model.Crc` exactly: `configure`, `load`, `shift`,
  with direction selecting both shift direction and tap position.
- [ ] **Step 2: Prove the recurrence** — one induction step showing the next state equals the
  shift-and-conditional-XOR of the current state for both directions.
- [ ] **Step 3: Test against the model** with the same CRC-16/CCITT, CRC-16/ARC and USB CRC5
  vectors from `tests/v3/test_model_crc.py`, driven bit by bit through the RTL.
- [ ] **Step 4: Run** `yosys -q formal/v3_crc.ys` and `make -C test -f Makefile.v3crc`.
- [ ] **Step 5: Commit** — `git commit -m "Add the shared bit-serial CRC unit"`

---

### Task 17: Engine RTL

The largest module. Build it in the order below, running the differential test after each sub-step
so a regression is attributable.

**Files:**
- Create: `src/v3_engine.v`
- Create: `formal/v3_engine.ys`
- Test: `test/v3/test_engine.py`, `test/support/v3_engine.py`, `test/Makefile.v3engine`

**Interfaces:**
- Produces: module `v3_engine`. Ports mirror the model's `Engine`: clock, reset, enable, `now`,
  `start`, `stop`, ownership and open-drain masks, the `v3_progmem` fetch interface, eight
  per-pin commit interfaces and `slot_free` inputs, per-pin `level`, `edge_time` and
  `edge_polarity` inputs, the queue interface, the CRC request/grant interface, and status outputs
  `running`, `halted`, `errors[15:0]`, `fault_code[7:0]`, `cursor[23:0]`, `pc[7:0]`.

- [ ] **Step 1: Build the harness first**

`test/support/v3_engine.py` wraps the DUT and a `model.Engine`, stepping both and comparing after
every edge. It must compare: `pc`, `cursor`, `regs[0..3]`, `running`, `halted`, `errors`, every
pin's `pending` slot and driven value, and the queue levels. On a mismatch, dump the last 32
cycles of both to `test/output/v3-divergence.json` and fail with the cycle number and the field
that diverged.

- [ ] **Step 2: Fetch and control flow only**

Implement `NOP`, `HALT`, `JMP`, `CALL`, `RET`, `DJNZ`, `FAIL`, `BRC`, `BREQ` and the fetch
pipeline. Run:

```bash
make -C test -f Makefile.v3engine COCOTB_TEST_FILTER=test_v3_engine_control_flow
```

Expected: pass. Confirm specifically that a taken branch takes two cycles and an untaken one takes
one, by reading the cycle counts the harness records.

- [ ] **Step 3: Registers and ALU**

Add `LI`, `MOV`, `ADD`, `SUB`, `AND`, `OR`, `XOR`, `SHL`, `SHR`. Run with filter
`test_v3_engine_alu`.

- [ ] **Step 4: Cursor and time**

Add `ADVANCE`, `ADVANCER`, `CURSORSET`, `CURSORGET`, `NOW`, `WAITTIME`. Run with filter
`test_v3_engine_time`. Assert in the test that `ADVANCE` never costs more than one cycle.

- [ ] **Step 5: Commits**

Add `SET`, `OE`, `DRIVE`, `OUT` with the ownership check and the multi-pin stall. Run with filter
`test_v3_engine_commit`.

- [ ] **Step 6: Inputs, queues, events, CRC**

Add the remaining instructions. Run the whole file.

- [ ] **Step 7: Full random differential**

```bash
make -C test -f Makefile.v3engine
```

Expected: all tests pass, including a 100,000-cycle random program differential.

- [ ] **Step 8: Formal**

`formal/v3_engine.ys` abstracts fetch as an arbitrary word arriving one cycle after the address
(`anyseq`, the same technique as `formal/v2_engine.ys` used). Proves: the two-cycle taken branch;
return stack depth never exceeds 2 and `RET` on an empty stack faults; queue levels stay within
0..16 and ordering is preserved; a commit strobe is only ever asserted for an owned pin; `errors`
bits are sticky.

```bash
yosys -q formal/v3_engine.ys
```

Expected: SUCCESS.

- [ ] **Step 9: Commit**

```bash
git add src/v3_engine.v formal/v3_engine.ys test/Makefile.v3engine \
  test/v3/test_engine.py test/support/v3_engine.py
git commit -m "Add the V3 engine with a cycle-exact differential against the model"
```

---

### Task 18: Host SPI RTL

**Files:**
- Create: `src/v3_host_spi.v`
- Create: `formal/v3_host_spi.ys`
- Test: `test/v3/test_host_spi.py`, `test/Makefile.v3host`

- [ ] **Step 1: Write the module.** Count SCK edges while CS_n is low. Accept the frame only on
  exactly 48. Any other count sets a frame error and the command is discarded. MISO is gated by
  the raw `cs_n`, as in V2, so the pin is released immediately on deselect rather than a
  synchronizer delay later.
- [ ] **Step 2: Prove** exact-48 acceptance, rejection at 47 and 49, two-flop synchronization of
  SCK, CS_n and MOSI, and that the response appears on the following frame.
- [ ] **Step 3: Test** with a cocotb SPI controller that clocks frames of 47, 48 and 49 bits, plus
  a randomized sequence comparing against `host.pack`/`host.unpack`.
- [ ] **Step 4: Run** both and commit — `git commit -m "Add the 48-clock host SPI interface"`

---

### Task 19: Capture RTL

**Files:**
- Create: `src/v3_capture.v`, `formal/v3_capture.ys`, `test/v3/test_capture.py`,
  `test/Makefile.v3capture`

Port from `git show v2:src/v2_capture.v`, widening the timestamp to the shared 32-bit value and
recording raw synchronized inputs, outputs, enables and engine flags.

- [ ] **Step 1: Port** — [ ] **Step 2: Prove** — [ ] **Step 3: Test** — [ ] **Step 4: Commit**

---

### Task 20: Top level

**Files:**
- Create: `src/v3_project.v`
- Create: `formal/v3_integration.ys`
- Test: `test/v3/test_project.py`, `test/Makefile.v3project`
- Modify: `info.yaml`

- [ ] **Step 1: Write `tt_um_mcranny_protocol_emulator_v3`**

Instantiate: one `v3_timebase`; eight `v3_pin_cell` and eight `v3_input_filter`; two `v3_progmem`;
two `v3_engine`; one `v3_crc` with round-robin arbitration; one `v3_capture`; one `v3_host_spi`.
Route `uio_in` through the filters into the cells, and the cells' `drive()` outputs to `uio_out`
and `uio_oe`. Combine each pin's two engine commit ports with the ownership masks so only the
owner can strobe.

- [ ] **Step 2: Update `info.yaml`**

```yaml
project:
  title: "Protocol Emulator ASIC V3"
  author: "Matthew Cranny"
  discord: ""
  description: "Two programmable engines scheduling pin changes against a shared 48 MHz timebase, with SRAM program memory"
  language: "Verilog"
  clock_hz: 48000000
  tiles: "6x4"
  top_module: "tt_um_mcranny_protocol_emulator_v3"
  source_files:
    - "v3_timebase.v"
    - "v3_pin_cell.v"
    - "v3_input_filter.v"
    - "v3_progmem.v"
    - "v3_crc.v"
    - "v3_engine.v"
    - "v3_host_spi.v"
    - "v3_capture.v"
    - "v3_project.v"
```

Keep `tiles: "6x4"` until the 8x4 template is confirmed (spec open item 2). The pinout block is
unchanged from V2 except the `uio` descriptions, which become
`"protocol pin N"` uniformly.

- [ ] **Step 3: Integration proof**

`formal/v3_integration.ys` black-boxes the engines and pin cells and proves: the two ownership
masks are disjoint; an engine cannot strobe a pin it does not own; `!ena` or `!rst_n` releases
every `uio_oe`; both engines START on the same cycle when commanded in one frame; program writes
are refused while an engine is running.

```bash
yosys -q formal/v3_integration.ys
```

- [ ] **Step 4: Full-chip differential**

`test/v3/test_project.py` loads a real program through the host SPI interface, starts both
engines, and compares every `uio` transition against the model for 200,000 cycles.

- [ ] **Step 5: Lint**

```bash
verilator --lint-only -Wall -Isrc src/v3_project.v --top-module tt_um_mcranny_protocol_emulator_v3
```

Expected: no warnings. Fix the RTL rather than adding waivers.

- [ ] **Step 6: Commit**

```bash
git add src/v3_project.v formal/v3_integration.ys info.yaml \
  test/Makefile.v3project test/v3/test_project.py
git commit -m "Add the V3 top level and integration proof"
```

---

## Phase 4 — Firmware and protocol oracles

### Task 21: Protocol peers

The peers are the independent oracles. They must be written from the protocol specifications, not
from the firmware, or they will agree with a bug.

**Files:**
- Create: `host/protocol_emulator/v3/peers.py`
- Test: `tests/v3/test_peers.py`

**Interfaces:**
- Produces: `UartReceiver`, `UartTransmitter`, `SpiTarget`, `SpiController`, `I2cTarget`,
  `I2cController`, `Ws2812Receiver`. Each takes a list of `(timestamp, level)` or
  `(timestamp, bus_state)` samples and returns decoded bytes plus a list of timing violations.

- [ ] **Step 1: Write the peer tests against synthetic waveforms**

Generate ideal waveforms in the test, decode them, assert the bytes come back. Then perturb: a
bit period 2% long, a missing stop bit, a glitch shorter than the filter window. Assert the peer
reports the violation rather than silently accepting.

- [ ] **Step 2: Implement the peers**
- [ ] **Step 3: Run** `python -m pytest tests/v3/test_peers.py -v`
- [ ] **Step 4: Commit** — `git commit -m "Add independent protocol decoders as test oracles"`

---

### Task 22: UART firmware

**Files:**
- Create: `host/protocol_emulator/v3/firmware/__init__.py`, `firmware/uart.py`
- Create: `examples/v3/uart_tx_115200.asm`, `examples/v3/uart_rx_115200.asm`
- Test: `tests/v3/test_firmware_uart.py`, `test/v3/test_uart.py`

**Interfaces:**
- Produces: `uart_tx(pin: int, baud: int) -> str` and `uart_rx(pin: int, baud: int) -> str`,
  returning assembly source. Registered in `firmware.PROGRAMS: dict[str, Callable[..., str]]`.

The TX loop, as the canonical demonstration of the firmware rule — schedule the edge, then advance
the cursor:

```text
# UART transmit, 8N1, one byte per host TX queue entry.
# R0 data shift register, R1 bit counter, R2 scratch, R3 the constant 1.
start:
PULL reg=0                        # blocks until the host supplies a byte
LI reg=1 value=8
LI reg=3 value=1
OUT pin={pin} level=0 offset=0    # start bit, scheduled at the cursor
ADVANCE ticks={bit}               # the cursor is now one bit period ahead
bit:
MOV dst=2 src=0
AND dst=2 src=3                   # isolate the least significant data bit
BREQ reg=2 value=0 target=zero
OUT pin={pin} level=1 offset=0
JMP target=next
zero:
OUT pin={pin} level=0 offset=0
next:
SHR dst=0 amount=1
ADVANCE ticks={bit}
DJNZ reg=1 target=bit
OUT pin={pin} level=1 offset=0    # stop bit
ADVANCE ticks={bit}
JMP target=start
```

**Why the two branches are not a problem.** `OUT` takes a literal level, so the data bit has to be
selected by branching. The two paths are not the same length: the one-bit path costs ten cycles
(`MOV` 1, `AND` 1, untaken `BREQ` 1, `OUT` 1, taken `JMP` 2, `SHR` 1, `ADVANCE` 1, taken `DJNZ` 2)
and the zero-bit path costs nine. **In V2 that asymmetry would have jittered the output.** Here it
cannot: the edge time is set by the cursor, which both paths advance by exactly one bit period, so
the instruction count is irrelevant to the waveform. This loop is the clearest demonstration in the
project of what the time-cursor architecture buys, and the documentation in Task 36 should use it.

The `OUT` at the top of each iteration stalls until the previous bit's slot frees, which happens
when that bit actually fires. That is the depth-1 slot doing the loop's flow control: the engine is
throttled to the bit rate with no synchronization instruction.

**Budget:** ten cycles of work against 48 cycles per bit at 1 Mbaud. Assert this in the test. Only
if the budget is ever exceeded should an `OUTR` instruction taking the level from a register bit be
considered, and that would be an ISA change requiring the opcode table, the generated header, the
model and the RTL to move together.

- [ ] **Step 1: Write the model-level test** asserting that the assembled program fits, and that
  running it in the model with a known byte queued produces a waveform the `UartReceiver` peer
  decodes back to that byte, with no timing violations and no missed-deadline flags.
- [ ] **Step 2: Run, fail, write the firmware, pass.**
- [ ] **Step 3: Measure the loop** and assert `cycles_per_bit <= 48` in the test, so a future edit
  that breaks 1 Mbaud fails loudly.
- [ ] **Step 4: Add the RTL-level cocotb test** running the same program on `v3_project` and
  decoding `uio` with the same peer.
- [ ] **Step 5: Add a deliberate-overrun test.** Queue bytes faster than the loop can send at a
  baud rate the loop cannot meet, and assert the missed-deadline flag sets and the edge still
  happens.
- [ ] **Step 6: Commit.**

---

### Task 23: SPI firmware

Controller and target, modes 0-3, at 1 MHz. Same structure as Task 22: model test with the peer,
then RTL test, then a duplex test using both engines.

- [ ] **Step 1-6** mirroring Task 22, with `tests/v3/test_firmware_spi.py` and
  `test/v3/test_spi.py`. Add a mode-matrix test that runs all four CPOL/CPHA combinations and
  asserts the sampling edge is correct in each.
- [ ] **Step 7: Commit.**

---

### Task 24: I2C firmware

Controller and target at 100 and 400 kHz, plus bus clear. All pins involved must be configured
open-drain; add a test asserting the firmware never requests a high drive on SDA or SCL — the pin
cell enforces it, but the firmware should not rely on that.

- [ ] **Step 1:** Model-level tests for: address + write, address + read, ACK, NACK, repeated
  START, STOP, arbitration loss, a target that clock-stretches, and the bus-clear sequence of nine
  SCL pulses followed by a STOP.
- [ ] **Step 2:** Implement, using `timing.I2C[100_000]` and `timing.I2C[400_000]`. No literal
  cycle counts.
- [ ] **Step 3:** Assert in the test that the measured SCL low and high times from the model trace
  match the table and clear the bus minimums.
- [ ] **Step 4:** RTL test with the `I2cTarget` peer on the other end.
- [ ] **Step 5: Commit.**

---

### Task 25: WS2812 firmware — the flexibility demonstration

This is the program that answers the judging criterion directly: a protocol the chip was not
designed for, implemented in firmware with no RTL change. Treat its documentation as part of the
deliverable.

**Files:**
- Create: `host/protocol_emulator/v3/firmware/ws2812.py`
- Create: `examples/v3/ws2812_rainbow.asm`
- Test: `tests/v3/test_firmware_ws2812.py`, `test/v3/test_ws2812.py`

- [ ] **Step 1: Write the test first**, asserting the measured high and low times of every emitted
  symbol fall inside the WS2812 datasheet windows (T0H 0.4 µs ±150 ns, T1H 0.8 µs ±150 ns, period
  1.25 µs), that the reset gap is at least 300 µs, and that the `Ws2812Receiver` peer recovers the
  exact GRB bytes.
- [ ] **Step 2: Implement** using `timing.WS2812`.
- [ ] **Step 3: Assert the per-bit budget.** 60 cycles per bit at 48 MHz. The loop must fit, and
  the test must fail if it does not.
- [ ] **Step 4: RTL test** driving three LEDs' worth of data and decoding it.
- [ ] **Step 5: Write `docs/flexibility.md`** showing the WS2812 program next to the UART program
  and pointing out that only the firmware differs. This is a submission artifact, not optional.
- [ ] **Step 6: Commit.**

---

## Phase 5 — Local Linux toolchain

### Task 26: Install and verify the flow toolchain

**None of WSL2, Docker, make, Icarus Verilog, Verilator, Yosys or LibreLane is installed on this
machine as of 19 October 2026.** This task installs them. It changes the machine, not the
repository, so get confirmation before running the installers.

- [ ] **Step 1: Install WSL2 with Ubuntu**

```powershell
wsl --install -d Ubuntu
```

Reboot when prompted. Verify:

```powershell
wsl -l -v
```

Expected: `Ubuntu  Running  2`.

- [ ] **Step 2: Install Docker Desktop with the WSL2 backend**

Follow https://tinytapeout.com/guides/local-hardening/. Verify from inside WSL:

```bash
docker run --rm hello-world
```

Expected: the "Hello from Docker!" message.

- [ ] **Step 3: Install the simulation and formal tools inside WSL**

```bash
sudo apt-get update
sudo apt-get install -y build-essential iverilog verilator yosys python3-venv
pip install --user cocotb==1.9.2 pytest==8.3.3
```

Verify each:

```bash
iverilog -V | head -1
verilator --version
yosys -V
cocotb-config --version
```

Record the versions in `docs/local-build.md`.

- [ ] **Step 4: Check out the PDK inside the WSL filesystem**

```bash
git clone https://github.com/IHP-GmbH/IHP-Open-PDK.git ~/pdk/ihp-open-pdk
cd ~/pdk/ihp-open-pdk && git log -1 --format=%H
ls -l ihp-sg13g2/libs.ref/ | grep sg13cmos5l_sram
```

Expected: `sg13cmos5l_sram` appears as a symlink (`l` in the first column). **It must be a
symlink.** If it is a plain directory or missing, the checkout did not preserve symlinks — do not
use an NTFS path or a zip download. Record the commit hash; it becomes spec open item 4.

- [ ] **Step 5: Confirm the 256x32 macro is present and read its geometry**

```bash
find ~/pdk/ihp-open-pdk -name 'RM_IHPSG13_1P_256x32_c2_bm_bist*' | sort
grep -A3 'SIZE' ~/pdk/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13cmos5l_sram/lef/RM_IHPSG13_1P_256x32_c2_bm_bist.lef | head
```

Expected: GDS, LEF, CDL, and three Liberty files. The LEF `SIZE` line should read
`SIZE 416.64 BY 118.78`. If it does not, the spec's area numbers are wrong and must be corrected
before the floorplan task.

- [ ] **Step 6: Extract the PDN column positions**

```bash
python3 - <<'PY'
import re, pathlib
lef = pathlib.Path.home() / "pdk/ihp-open-pdk/ihp-sg13g2/libs.ref/sg13cmos5l_sram/lef/RM_IHPSG13_1P_256x32_c2_bm_bist.lef"
text = lef.read_text()
for supply in ("VDD!", "VDDARRAY!", "VSS!"):
    block = re.search(rf"PIN {re.escape(supply)}(.*?)END {re.escape(supply)}", text, re.S)
    if not block:
        print(f"{supply}: not found")
        continue
    xs = sorted({float(m.group(1)) for m in re.finditer(r"RECT ([\d.]+) [\d.]+ ([\d.]+) [\d.]+", block.group(1))})
    print(f"{supply}: {len(xs)} columns, x = {xs[:8]}{' ...' if len(xs) > 8 else ''}")
PY
```

Save the output to `physical/v3/pdn-columns.txt`. These are the numbers the PDN pitch and offset
must be derived from. **Do not use the 512x32 values from the published walkthrough** — the spec
is explicit that V2's 50 µm pitch misses the columns entirely.

- [ ] **Step 7: Re-run the full test suite from WSL**

```bash
cd /mnt/c/Users/matthewc/Documents/Projects/protocol-emulator-asic
python3 -m pytest tests -q
for mk in test/Makefile.v3*; do make -C test -f "$(basename "$mk")" || break; done
for proof in formal/v3_*.ys; do yosys -q "$proof" || break; done
```

Expected: everything that passed on Windows still passes, plus the simulation and formal tests that
could not run there.

- [ ] **Step 8: Fix the three Windows portability defects found in V2 tooling**

Manifest keys must be POSIX-style (`path.as_posix()`, never `str(path)`); tests that create
symlinks must skip cleanly when Developer Mode is off rather than erroring; make-based targets
need a Python entry point so they are runnable on Windows. Add a test for each.

- [ ] **Step 9: Document and commit**

Update `docs/local-build.md` with the exact versions, the PDK commit hash, and the WSL/Windows
split. Commit the documentation and the portability fixes; the installed tools are outside the
repository.

```bash
git add docs/local-build.md physical/v3/pdn-columns.txt tests/
git commit -m "Document the local WSL flow toolchain and fix Windows test portability"
```

---

## Phase 6 — Physical implementation

### Task 27: Macro integration and floorplan

**Files:**
- Create: `physical/v3/config.json`, `physical/v3/overrides.json`, `physical/v3/info.yaml`
- Create: `physical/v3/macro.json`
- Test: `tests/tooling/test_v3_physical_config.py`

- [ ] **Step 1: Write the config test first**

Assert, by reading the JSON: `CLOCK_PERIOD` is `20.833`; `MAX_TRANSITION_CONSTRAINT` is `1.5`;
`PL_RESIZER_HOLD_SLACK_MARGIN` and `GRT_RESIZER_HOLD_SLACK_MARGIN` are `0.10`; `RUN_MAGIC_DRC` is
`0`; `RUN_KLAYOUT_DRC` is `1`; `ERROR_ON_ILLEGAL_OVERLAPS` is `0`; there are exactly two macro
instances; each has exactly two `PDN_MACRO_CONNECTIONS` entries naming `VDD!` and `VDDARRAY!`;
`MACRO_PLACE_HALO` is `[2, 2]`; the three Liberty corners are mapped, with the SRAM fast corner
(characterized at −55 °C) mapped onto `nom_fast_1p32V_m40C`.

Also assert `FP_PDN_VPITCH` is **not** `50.0` — V2's value, which the spec identifies as missing
the macro supply columns — and that the pitch and offset are consistent with
`physical/v3/pdn-columns.txt` from Task 26 Step 6.

- [ ] **Step 2: Write the configuration** derived from `src/config.json` as the base, with the
  macro, PDN and timing changes above.
- [ ] **Step 3: Place the macros.** Both on the left edge, signal-pin faces toward each other,
  with the fetch wrappers in the channel. Set `MACRO_PLACEMENT_CFG` with explicit coordinates
  rather than letting the placer choose.
- [ ] **Step 4: Add routing obstructions** on Metal2 through Metal4 over each macro footprint.
- [ ] **Step 5: Add one antenna diode per SRAM input** before detailed placement.
- [ ] **Step 6: Run the config test.**
- [ ] **Step 7: Commit.**

---

### Task 28: First hardening run and area measurement

- [ ] **Step 1: Run synthesis only, at 6x4**

```bash
python tools/v3_hardening.py --tiles 6x4 --stop-after Yosys.Synthesis
```

- [ ] **Step 2: Record the measured standard-cell area**

```bash
python tools/v3_hardening.py --report-area
```

Compare against the spec's estimate of about 567,000 µm² of standard cells. **Replace the spec's
estimated numbers with the measured ones** in `docs/superpowers/specs/2026-10-19-v3-design.md`
section 4 and commit that correction with the measurement. The spec says these are estimates to be
replaced at first synthesis; this is that moment.

- [ ] **Step 3: Decide 6x4 versus 8x4** from the measured utilization against the 65% ceiling.
- [ ] **Step 4: Run the full flow**

```bash
python tools/v3_hardening.py --tiles 6x4 --full 2>&1 | tee build/harden-6x4.log
```

Record wall-clock time, peak memory, and whether `/mnt/c` I/O was the bottleneck. If it was, move
the run directory into the WSL filesystem and copy artifacts back to `build/`.

- [ ] **Step 5: Verify both macros are powered**

Open the layout and confirm PDN stripes land on the supply columns. Read the IR-drop report. **An
unpowered macro passes DRC and LVS** — this check is the only thing that catches it. Save a
screenshot to `evidence/v3-first-harden/pdn.png`.

- [ ] **Step 6: Commit the evidence and the corrected spec numbers.**

---

### Task 29: Timing closure at 48 MHz

- [ ] **Step 1: Run STA at all three corners** and capture the worst setup and hold slack per
  corner.
- [ ] **Step 2: Resolve spec open item 1.** Inspect the Tiny Tapeout SDC and determine whether
  input and output delay are specified as a fraction of the clock period or as absolute values.
  Record the answer in the spec. If it is a fraction, external paths scale with the faster clock
  and may not close; if absolute, they are unaffected. This determines whether 48 MHz is reachable
  on the I/O paths at all.
- [ ] **Step 3: Close setup** to at least +2 ns register-to-register at the slow corner.
- [ ] **Step 4: Close hold** to at least +0.10 ns at the fast corner.
- [ ] **Step 5: Investigate the tie cells.** V2's netlist carried 5,825 `tiehi` instances. Check
  what V3 synthesis produces and whether it is justified. Record the finding.
- [ ] **Step 6: Commit the signoff report.**

---

### Task 30: Precheck, DRC, LVS and the release run

- [ ] **Step 1: Run precheck**, whitelisting marker layers 25/0 and 16/0.
- [ ] **Step 2: Run KLayout DRC** with the CMOS5L deck. Magic DRC stays off.
- [ ] **Step 3: Run LVS.**
- [ ] **Step 4: Gate-level simulation** with the SRAM behavioral model linked, running the full
  differential suite against the netlist.
- [ ] **Step 5: Attempt formal equivalence**

```bash
eqy -f build/v3_eqy.eqy
```

If `eqy` does not work with this flow, fall back to the gate-level differential plus the hash
chain, and **record in the verification write-up which was used**. Do not claim equivalence
checking that was not performed.

- [ ] **Step 6: The release run must be fresh from RTL.** Not a checkpoint resume. Delete the run
  directory and run end to end. Record source, netlist and GDS hashes.
- [ ] **Step 7: Commit the evidence bundle.**

---

## Phase 7 — FPGA prototype

**The board has not been purchased. Nothing in Phases 1-6 or 8 waits on this phase.** Tasks 31 and
32 are written so Task 31 is fully completable today.

### Task 31: FPGA wrapper RTL and simulation — no board required

**Files:**
- Create: `fpga/arty_a7_35t/top.v`, `bram_progmem.v`, `uart_spi_bridge.v`, `arty_a7_35t.xdc`
- Test: `test/v3/test_fpga_progmem.py`, `test/Makefile.v3fpgamem`

- [ ] **Step 1: Write `bram_progmem.v`** with the identical interface and timing contract as
  `v3_progmem.v`: registered read valid at N+1, no simultaneous read and write to one address,
  enable low while stalled.
- [ ] **Step 2: Run the Task 15 test suite against it unchanged.** Same Makefile, different
  `VERILOG_SOURCES`. If any test fails, the contract differs and the FPGA would not be comparable
  to the ASIC.

```bash
make -C test -f Makefile.v3fpgamem
```

Expected: the same 6 tests pass.

- [ ] **Step 3: Write the MMCM instantiation.** 100 MHz in, 48 MHz out: `CLKFBOUT_MULT_F = 12.0`
  for a 1200 MHz VCO, `CLKOUT0_DIVIDE_F = 25.0`. Verify 1200 MHz is inside the Artix-7 −1 speed
  grade VCO range (600-1200 MHz) before committing; it is at the top of the range, so if Vivado
  rejects it, use `CLKFBOUT_MULT_F = 9.6` with `DIVCLK_DIVIDE = 1` and `CLKOUT0_DIVIDE_F = 20.0`
  for a 960 MHz VCO.
- [ ] **Step 4: Write `uart_spi_bridge.v`** so the Python host drives the real chip SPI pins over
  a USB UART, rather than a separate debug path. The ASIC's host interface must be the one
  exercised.
- [ ] **Step 5: Simulate the full FPGA top** running a UART program, comparing against the model.
- [ ] **Step 6: Commit** — `git commit -m "Add the Arty A7-35T wrapper, simulated without hardware"`

---

### Task 32: Board bring-up — blocked on hardware

**Do not start until an Arty A7-35T is physically available. If it arrives too late, this task is
cut and the write-up states the FPGA comparison was not performed.**

- [ ] **Step 1: Synthesize and check utilization.** Confirm the design fits the XC7A35T.
- [ ] **Step 2: ILA budget.** About 2048 samples at roughly 200 probe bits is the practical limit
  on this part. Plan narrow, repeated captures rather than one wide one.
- [ ] **Step 3: Bring-up check 1 — fetch.** ILA must show the one-cycle read latency and a taken
  branch costing exactly two cycles.
- [ ] **Step 4: Bring-up check 2 — pin cell.** ILA must show a commit firing on its due tick, and
  a deliberately late commit applying immediately with the missed flag set.
- [ ] **Step 5: Bring-up check 3 — host frame.** ILA must show a 48-clock frame accepted and a
  47-clock frame rejected.
- [ ] **No protocol trace counts as evidence until all three pass.** Then compare every shipped
  program's pin trace and capture records against the model, cycle by cycle.
- [ ] **Step 6: Record honestly** that this validates logic and firmware, not pad delays or ASIC
  timing.
- [ ] **Step 7: Commit the capture evidence.**

---

## Phase 8 — Verification gates and submission

### Task 33: Mutation gate

**Files:**
- Create: `tools/v3_mutations.py` (modelled on `git show v2:tools/v2_mutations.py`)
- Test: `tests/tooling/test_v3_mutations.py`

Each mutation must cause a **named** test to fail. A compile error, a parser failure, or a missing
results file does not count as detection — the V2 tool already enforces this and V3 keeps it.

| Mutation | File | Change | Must fail |
|---|---|---|---|
| `unsigned_compare` | `v3_pin_cell.v` | drop `$signed` from the deadline compare | `test_v3_pin_cell_past_due_applies_and_flags` |
| `fire_early` | `v3_pin_cell.v` | `<= 0` to `<= 1` | `test_v3_pin_cell_does_not_fire_early` |
| `no_missed_flag` | `v3_pin_cell.v` | delete the `missed <= 1'b1` assignment | `test_v3_uart_late_commit_sets_the_missed_flag` |
| `one_cycle_branch` | `v3_engine.v` | remove the branch bubble | `test_v3_engine_taken_branch_costs_two_cycles` |
| `wrong_branch_target` | `v3_engine.v` | `target` to `target + 1` | `test_v3_engine_control_flow` |
| `validity_never_set` | `v3_progmem.v` | hold the bitmap write low | `test_v3_progmem_image_is_incomplete_until_every_word_is_written` |
| `spi_wrong_edge` | firmware/`v3_engine.v` | invert the sampling edge | `test_v3_spi_mode_matrix` |
| `uart_stop_bit` | firmware | drop the stop bit | `test_v3_uart_differential` |
| `queue_off_by_one` | `v3_engine.v` | `<= 16` to `<= 17` | `test_v3_engine_queue_boundary` |
| `capture_timestamp` | `v3_capture.v` | `+ 1` to `+ 2` | `test_v3_capture_differential` |
| `crc_wrong_tap` | `v3_crc.v` | tap bit 14 instead of 15 | `test_v3_crc_ccitt_vector` |
| `missing_reset` | `v3_engine.v` | drop a state register from the reset branch | `test_v3_reset_audit` |

**On `unsigned_compare`:** dropping `$signed` leaves future and exactly-due deadlines behaving
identically, so the wrap test does not catch it. The only behavioural difference is in the
past-due case, where an unsigned subtraction yields a huge positive distance and the cell waits
roughly 349 ms instead of applying immediately. That is why it maps to the past-due test and not
the wrap test — worth knowing, because the obvious mapping is the wrong one.

- [ ] **Step 1: Write the tool** following the V2 structure: copy the source, apply a unique
  textual anchor replacement (fail loudly if the anchor is not unique), run only the named test,
  require a genuine assertion failure.
- [ ] **Step 2: Run it.**

```bash
python tools/v3_mutations.py --output build/v3-mutations
cat build/v3-mutations/results.json
```

Expected: every entry has `"detected": true`. An undetected mutation means a verification gap, not
a tool problem — write the missing test.

- [ ] **Step 3: Commit.**

---

### Task 34: Cross-engine and protocol corner cases

- [ ] **Step 1: Cross-engine tests.** Simultaneous START; a barrier expressed as both cursors
  advanced to the same tick, asserting both engines' commits land on that exact tick; events
  before, during and after synchronization; one engine held in reset or stalled while the other
  runs unaffected.
- [ ] **Step 2: Protocol corner cases.** UART back-to-back frames, a bad stop bit, reset
  mid-frame. SPI edge alignment, CS transitions mid-byte, back-to-back transfers. I2C ACK, NACK,
  repeated START, STOP, bus busy, arbitration loss, a stuck line. Invalid opcodes and boundary
  addresses. Reset during an active transaction on each protocol.
- [ ] **Step 3: Reset and unknown-state audit.** Port `tools/validate_reset.py` and
  `tools/audit_initialization.py` to V3.
- [ ] **Step 4: Run everything and commit.**

---

### Task 35: Constrained-random and coverage — only if the required set is green

**Cut this first if time is short.** Do not start it while any required gate is red.

- [ ] **Step 1: Random program generator** drawing legal instructions from the opcode table, with
  the seed recorded in the results and printed on failure.
- [ ] **Step 2: Random protocol traffic** against the peers, both engines active.
- [ ] **Step 3: Functional coverage** over: every opcode, both outcomes of every branch, all four
  SPI modes, UART framing and error cases, every I2C bus condition, queue wraparound, capture
  trigger and wraparound, timebase wraparound, and cross-engine contention.
- [ ] **Step 4: Promote every minimized failure to a directed test.**
- [ ] **Step 5: Commit.**

---

### Task 36: Documentation and submission package

**Files:**
- Create: `docs/v3-architecture.md`, `docs/v3-isa-reference.md`, `docs/v3-toolchain.md`,
  `docs/v3-verification.md`, `docs/flexibility.md`, `docs/ai-use.md`
- Modify: `docs/index.md`, `docs/info.md`, `README.md`

- [ ] **Step 1: Generate the ISA reference from the opcode table.** Hand-written tables drift; a
  generated one cannot. Add a drift test like Task 7's.
- [ ] **Step 2: Write the architecture document** explaining the time-cursor model, why pin cells
  hold the deadline rather than the engine, and what the depth-1 slot buys.
- [ ] **Step 3: Write the verification document.** State plainly what is **proved**, what is
  **assumed**, what is **simulated only**, and whether the FPGA comparison happened. Do not blur
  the categories — this is the section a judge reads to calibrate trust in everything else.
- [ ] **Step 4: Write `docs/ai-use.md`.** The competition requires describing AI tool use. State
  which tools, for what, and what was human-reviewed.
- [ ] **Step 5: Write the toolchain and example guide,** including the external hardware needed:
  I2C pull-up values, the WS2812 level shifter, any Pmods.
- [ ] **Step 6: Update `docs/info.md`** — the Tiny Tapeout datasheet. It currently contains a
  stale V1 statement; it must describe V3 only.
- [ ] **Step 7: Verify the strict docs build**

```bash
python -m sphinx -n -W -q -b html docs build/docs
```

Expected: exit 0. `-W` makes every warning fatal, so a broken cross-reference fails the build.

- [ ] **Step 8: Final evidence bundle.** Source, netlist and GDS hashes from the fresh run; the
  precheck, DRC, LVS and STA reports; the mutation results; the formal proof logs.
- [ ] **Step 9: Commit.**

```bash
git add docs README.md
git commit -m "Add V3 architecture, ISA, toolchain, verification and AI-use documentation"
git log --format='%an <%ae>' | sort -u
```

Expected from the last command: exactly one line,
`Matthew Cranny <crannymatthew@gmail.com>`, for every V3 commit.

---

## Cut order

If time runs short, cut in this order. Each line is independently droppable without invalidating
what remains.

1. Constrained-random and functional coverage (Task 35).
2. The independent Rust model — not scheduled in this plan; it starts only after 48 MHz closes at
   8x4 with the required tests green.
3. SDF gate-level simulation (part of Task 30).
4. `eqy` formal equivalence, falling back to the gate-level differential and hash chain (Task 30
   Step 5).
5. FPGA board comparison if the hardware arrives late (Task 32).

Low-speed USB, 1-Wire and Ethernet are outside the submission entirely. The CRC unit is built
anyway because USB is planned after the deadline.

---

## Self-review

**Spec coverage.** Section 1 goal and clean break: Task 2. Section 2 architecture: Tasks 8, 12, 13,
14, 16, 19, 20. Section 3 ISA and fetch: Tasks 3, 5, 6, 7, 9, 15, 17. Section 4 memory and
physical: Tasks 15, 27, 28, 29, 30. Section 5 toolchain and firmware: Tasks 4, 6, 11, 21, 22, 23,
24, 25. Section 6 verification: Tasks 12-20 (formal), 17 and 20 (differential), 33 (mutation), 34
(corner cases and cross-engine), 35 (random and coverage), 29 and 30 (timing and signoff). Section
7 FPGA: Tasks 31, 32. Section 8 submission: Task 36. Section 9 development environment: Tasks 1,
26. Section 10 priorities: the cut order above. Section 11 open items: item 1 in Task 29 Step 2,
item 2 in Task 20 Step 2, item 3 in Task 26 Step 6, item 4 in Task 26 Step 4, item 5 in Task 29
Step 5, item 6 is external and unresolvable here, item 7 in Task 28 Step 4.

**Gaps I am flagging rather than papering over.**

- **Task 9 does not contain the full `Engine` implementation code.** It specifies the interface,
  the required attributes, the exact ordering inside `step()`, and sixteen tests that pin the
  behaviour, but the body is described rather than written. The engine is several hundred lines
  and writing it blind, before the pin cell and program memory models are exercised, would produce
  code that gets rewritten. The tests are complete and executable, which is what actually
  constrains the implementation.
- **Tasks 13, 17, 18, 19 and 20 describe RTL rather than listing it.** Same reason, plus these
  cannot be run on this machine until Task 26 installs the toolchain, so there is no way to verify
  any Verilog this plan might contain. The property tables and test lists are complete.
- **Tasks 27-30 depend on the PDK geometry read in Task 26.** The PDN numbers genuinely cannot be
  written down before someone reads the LEF. Task 26 Step 6 produces them and Task 27 Step 1
  asserts against them.
- **The UART data-bit encoding is an open design question** (Task 22). Both resolutions are
  specified with a measurement that decides between them, rather than guessing now.

**Type consistency.** `Image`, `Instruction`, `Pending`, `Frame`, `Op` and `Field` are defined once
and used with the same field names throughout. `signed_distance(due, now)` keeps that argument
order in every call. `cycles(seconds, tolerance)` and `bit_cycles(baud, tolerance)` are consistent.
The RTL `slot_free` corresponds to the model's `PinCell.free`, and `image_complete` to
`ProgramMemory.valid`; those two name pairs differ deliberately because the RTL names are wire
names, and the correspondence is stated in Tasks 13 and 15.
