# Track 2: verification after the V2 candidate baseline

Track 2 starts after the V2 candidate baseline is frozen. These are planned
verification deliverables, not properties claimed by the V2 release candidate.

The first deliverable is a reviewed property-to-implementation map. For each
property, record the ISA clause, assembler encoding/decoder function, reference
model transition, RTL state/transition, executable regression, proof statement,
assumptions, and any abstraction gap. Include negative examples demonstrating
what the statement does not establish.

The work includes:

- Expanded RTL properties for host transport, program storage, capture contents,
  reset/restart sequencing, and protocol execution/timing boundaries.
- Review of every integration black-box assumption against implementation and
  separate proofs, especially host reset behavior, readiness, and FIFO bounds.
- Broader mutation testing tied to those properties; retain the existing six
  mutants, including ownership overlap, and document undetected changes.
- A Lean model of ISA syntax, encoding/decoding, execution state, and timing.
  Start with encoding validity and round-trip theorems, then transition and
  instruction timing statements with explicit preconditions and fault behavior.

Lean definitions must be related explicitly to the Python assembler/reference
model and RTL. State whether correspondence is proved, generated from a common
specification, exhaustively checked over a bounded domain, or supported only by
test vectors. A theorem about the Lean model alone is not a proof of the shipped
RTL. The map must keep those evidence levels visible.

Acceptance of Track 2 requires reproducible proof commands, pinned Lean/tool
versions, proof outputs, mutation sensitivity, and a written account of remaining
assumptions. It must not delay or silently enlarge the V2 candidate's declared
verification scope.
