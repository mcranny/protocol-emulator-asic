# Test migration from 3d5210a

The frozen record predates refactoring and accounts for all 583 parametrized
pytest cases and 24 cocotb scenarios. Its original paths are historical
identifiers, preserved verbatim. The deprecated-case deletion list is empty:
all 32 original pytest modules cover retained behavior or active tools.
V1 compatibility, obsolete Metal1 prevention, and custom repair checks remain.
Only their obsolete module locations are removed.

`frozen.json` records each destination, definition body hash, assertions,
parameters, fixture dependencies, and called operations. One planned name
is corrected for grammar: `test_release_requirements_is_not_weakened` becomes
`test_release_requirements_are_not_weakened`. Parameter values remain unchanged;
explicit IDs label their argument names and original values.

`verify.py` compares collection identities, test bodies, parameter expressions,
and 21 extracted helper bodies against the retained baseline source snapshot.
It permits only named factory-call substitutions, suite/path renaming, import
reorganization, the specific relocated repository-root lookup, and two
relocated cocotb artifact paths that must continue writing to `test/output`. It also
checks 38 baseline files covering RTL, firmware, formal, physical configuration,
and the Verilog testbench byte for byte. `checked.json` records the result.

`coverage.json` records the executable coverage comparison: each production
file has exactly the same executed lines and branch arcs. The measurement
includes unexecuted physical tools and is not a physical qualification claim.
No new cases were added to offset deletions; none were deleted.

Run from the repository root with the pinned test dependencies installed:

```sh
python evidence/test-migration-3d5210a/verify.py
```

The audit expects the original clean functional run under
`build/release-preparation/baseline/`, including its source snapshot and
`stage.json`. Recreate that baseline only from the recorded commit in a
separate checkout. Preserve the existing baseline directory.

Baseline and refactored Python coverage were measured using coverage 7.10.6:

```sh
python -m coverage run --branch --source=host/protocol_emulator,tools -m pytest -q
python -m coverage json -o coverage.json
```

The separate retained runs use distinct data files and output directories.
Full validation results belong in the corresponding functional stage manifest;
this migration record does not claim final clean-revision release acceptance.

The first post-refactor full gate passed every command but correctly failed
its final source-stability check: two relocated scenarios wrote six generated
JSON files into `test/v2/output`. That failed run is retained under
`build/release-preparation/step-1`; the path correction is audited explicitly.
The subsequent full gate uses `build/release-preparation/step-1-retry-1`.
