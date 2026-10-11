# Submission deliverables

The deliverables below describe implemented behavior and reproducible verification.
Competition submission is a separate action after the candidate is qualified.

| Deliverable | Documentation and executable material |
| --- | --- |
| Architecture | [Engines, memory, pins, timing and host interface](v2-architecture.md); [reset and state loss](reset-and-power.md) |
| ISA | [Encoding, operands, timing, errors and compatibility](isa-reference.md) |
| Assembler/toolchain | [Installation, V2 Python APIs, images and loading](assembler-toolchain.md) |
| Example programs | [UART, SPI modes 0–3, I2C 100/400 kHz matrix](example-programs.md), generated assembly/images and checked traces |
| Verification write-up | [Independent models, simulation, formal, mutations and limits](verification.md); [proof assumptions](formal-proofs.md) |
| Physical implementation | [Stock TT procedure and repair provenance](v2-tt-flow.md); [release evidence index](release-evidence.md) |
| TT Markdown datasheet | [Project datasheet](info.md), retained at `docs/info.md` |

HTML is built with pinned Sphinx, MyST and Alabaster dependencies and retained as
an Actions artifact. Pages deployment is deferred. Source, package and example
archives, checksums, and the final same-revision evidence index remain release
qualification work until [the checklist](release-evidence.md) is complete.
