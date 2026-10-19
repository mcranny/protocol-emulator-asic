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
isa-reference
assembler-toolchain
example-programs
host-api
reset-and-power
info
```

```{toctree}
:maxdepth: 2
:caption: Verification and release

verification
formal-proofs
release-evidence
release-notes
```

```{toctree}
:maxdepth: 1
:caption: Reference and historical evidence

validation
physical-closure
local-build
gds-viewer
```
