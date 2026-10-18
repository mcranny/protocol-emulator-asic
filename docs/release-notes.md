# V2.0.0rc1 release candidate notes

These notes describe an unpublished candidate targeting eventual `v2.0.0`.
The selected physical release is the validated local TT checkpoint recovery
from `447b3df`; software/package evidence is bound separately to `881495c`.
No silicon/FPGA demonstration or competition acceptance is claimed.

## Candidate behavior

V2 provides two independently programmable 25 MHz timing engines with 64 words
of 24-bit instruction memory per engine, 16-entry TX/RX queues, SPI host control,
pin ownership and open-drain configuration, input filtering, and 32 triggered
capture records. Firmware supports UART TX/RX, SPI modes 0–3 in controller and
target roles, and I2C 100/400 kHz in controller and target roles. See the
[architecture](v2-architecture.md), [ISA](isa-reference.md), and
[executable examples](example-programs.md) for detailed behavior and limits.

Default TT metadata, physical configuration, root Makefile targets and default
pin simulation select V2. Explicit `v1-*` regression targets, V1 RTL and the
prior V1 physical metadata/configuration remain available. Package metadata and
runtime version are `2.0.0rc1`.

The physical target remains 6×4 tiles, 40 ns clock, maximum transition 1.5 ns,
clock uncertainty 0.25 ns, and 32 capture records. Root physical settings
incorporate the existing V2 overrides without changing the prepared physical
settings. The checkpoint recovery's execution/debug settings remain specific to
that recorded run; any future flow change requires explicit review and fresh
qualification. The 8×4 template and any migration/re-hardening for it are deferred.

## Compatibility and operating limits

- The installed `protocol-emulator` CLI remains V1. V2 assembly, disassembly,
  model execution and loading use the existing Python APIs. Package versioning
  does not alter protocol selection or host version-query responses.
- V1 instruction compatibility does not imply binary compatibility of the host
  command protocol, queue sizes or status layout. Use the appropriate backend.
- Program/capture storage is volatile. After state loss: select the design,
  reset, reconfigure ownership/open-drain/filter state, reload programs and
  queues, then start execution. Logical disable does not promise power retention.
- Continuous arbitrary 1 Mbaud UART duplex cannot be sustained through the
  1 MHz SPI host; the documented 1 Mbaud examples are bounded/preloaded bursts.
- The combined I2C controller program uses all 64 words; the target uses 62.
  Input filtering requires matching firmware configuration. Digital protocol
  tests do not establish analog rise-time performance or host scheduling guarantees.

## Verification and evidence

The refactor retained all 583 original pytest cases and 24 cocotb scenarios,
with unchanged measured production coverage at the migration checkpoint.
Five induction proofs, six mutations, initialization elaboration, four-state
reset tests, independent models/decoders and executable examples are documented
in the [verification write-up](verification.md).

The selected `447b3df` checkpoint recovery passed physical checks, nine stock
prechecks, all 14 V2 gate-level pin scenarios, mapping and full-geometry checks.
A supplemental reset audit from `c1c9be5` passed on its netlist. That evidence
retains those source identities. On 18 October 2026, after the six-hour hosted
timeout for `881495c`, the repository owner selected this layout for V2.
An exact Yosys elaboration comparison (excluding only source-location
attributes) and prepared-configuration comparison passed between the two sources.
See the [release evidence index](release-evidence.md) for hashes, separate
software/physical identities and the retained failed attempts.

## Qualification and publication checklist

- Review and commit the release-selection documentation with authorization,
  after its complete functional gate and strict Sphinx build pass.
- Retain the selected layout's passing physical checks, stock precheck,
  all-corner timing/electrical checks and complete full-geometry evidence.
- Complete the current gate-level and reset/X reruns on the selected netlist;
  record their test revision separately from the physical source.
- Preserve the original source and dependency identities, both hosted timeouts,
  the failed local parent route and the exact successful checkpoint recovery.
- Produce a source archive, Python sdist/wheel, Sphinx HTML, generated example
  sources/images/traces, evidence index and SHA-256 checksums. Verify package
  version consistency and installed APIs/CLI in an isolated environment.
- Deliver the reviewable candidate and its evidence for publication approval.
  Merge, tags, release publication and competition submission remain separate.

[Track 2](verification-track-2.md) starts after the V2 candidate baseline is
frozen. Expanded RTL proofs and Lean theorems are future work, not RC claims.
