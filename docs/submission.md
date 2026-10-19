# Submission deliverables

The deliverables below describe implemented behavior and reproducible verification.
Competition submission is a separate action after the candidate is qualified.

| Deliverable | Documentation and executable material |
| --- | --- |
| Architecture | Engines, memory, pins, timing and host interface; [reset and state loss](reset-and-power.md) |
| ISA | [Encoding, operands, timing, errors and compatibility](isa-reference.md) |
| Assembler/toolchain | [Installation, V2 Python APIs, images and loading](assembler-toolchain.md) |
| Example programs | [UART, SPI modes 0–3, I2C 100/400 kHz matrix](example-programs.md), generated assembly/images and checked traces |
| Verification write-up | [Independent models, simulation, formal, mutations and limits](verification.md); [proof assumptions](formal-proofs.md) |
| Physical implementation | Stock TT procedure and repair provenance; [release evidence index](release-evidence.md) |
| TT Markdown datasheet | [Project datasheet](info.md), retained at `docs/info.md` |

HTML is built with pinned Sphinx, MyST and Alabaster dependencies and retained as
an Actions artifact and published on [GitHub Pages](https://mcranny.github.io/protocol-emulator-asic/),
with the [interactive layout explorer](https://mcranny.github.io/protocol-emulator-asic/layout/). Source, package and example
archives and checksums identify the software candidate separately from the
selected physical source. The [release evidence index](release-evidence.md)
records the layout, source correspondence and publication checklist.
