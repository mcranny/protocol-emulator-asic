# Verification

The question the checks answer is when a pin changed.

The Python model is the cycle-accurate reference. `python -m pytest tests -q`
runs it, the assembler, the loader and the host frame codec. `python -m mypy`
type-checks the package.

Each RTL block has a cocotb suite (`test/Makefile.v3*`) and an induction proof
(`formal/v3_*.ys`). Both run in WSL. The proof passes only when Yosys reports
the induction step proven; a bounded pass is not enough. The properties and
their assumptions are in [the architecture](design/v3-architecture.md#verification).

The top-level suite loads both programs over the SPI port, starts both engines
and compares every `uio` transition with a model built from the same blocks. It
also pushes bytes with `TXPUSH` and reads records back with `RXPOP`, checking
the round trip and the empty-queue rejection against the model. Directed tests
cover the input-filter mask, capture arm, stop and record readback, and each
`STATUS` address. The integration proof black-boxes the engines and the pin
cells and proves the ownership, release, atomic-start, write-refusal and
filter-mask properties of the wiring.

Two model engines sharing one set of pins are checked for a commit from each
landing on the same tick, a stall in one leaving the other running, and an
input event that is absent before its edge, sticky after it, and invisible to
the engine that is not watching.

What is proved is the induction listed above. What is simulated is the cocotb
suites and the Python model, including the firmware programs against protocol
peers. What is not claimed: pad delay, a routed ASIC timing result, an FPGA
comparison, or a mutation result. The [architecture](design/v3-architecture.md#verification)
table names each proof.
