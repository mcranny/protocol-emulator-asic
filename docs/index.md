# Protocol Emulator ASIC V2

V2 implements two programmable timing engines, SPI host control, and triggered
capture for reproducible hardware debugging. Protocols are ordinary instruction
programs. The release candidate selects the validated local TT checkpoint
recovery as its physical basis; see the [release evidence](release-evidence.md)
for source identities, checks and limitations. No hardware demonstration is claimed.

```{toctree}
:maxdepth: 2
:caption: Design and use

submission
v2-architecture
isa-reference
assembler-toolchain
example-programs
host-api
reset-and-power
v2-capture
v2-replay
v2-scenarios
v1-compatibility
info
```

```{toctree}
:maxdepth: 2
:caption: Verification and release

verification
formal-proofs
v2-tt-flow
release-evidence
release-notes
verification-track-2
v2-validation
```

```{toctree}
:maxdepth: 1
:caption: Reference and historical evidence

v1-specification
v2-design-evolution
validation
physical-closure
local-build
gds-viewer
```
