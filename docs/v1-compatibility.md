# V1 compatibility

V1 remains an explicit regression target with its [specification](v1-specification.md)
and historical `v1.0.0` artifacts. V2 retains opcodes 0–C and their timing for
V1-valid programs, but changes the host protocol, queue sizes and status layout.
The version query distinguishes V1 `0x010140` from V2 `0x020240`.

Use `protocol_emulator.v2.host.connect(transport)` to select the matching host
backend. V1 clients are not promised binary host compatibility with V2. The
installed CLI remains V1; use [the V2 Python toolchain](assembler-toolchain.md)
for the new firmware. Promotion of ASIC build defaults will not change CLI or
Python API behavior.

The full functional runner always includes both V1 and V2. Existing V1 core
and pin cases remain required, alongside both tops' lint/synthesis and the V1
induction and reset checks. V1's four-byte TX buffering and request/NOP throughput
limits must not be confused with V2's packed writes and 16-entry queues.
