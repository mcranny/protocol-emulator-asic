# Successful V2 local acceptance evidence

This record describes the frozen source `af573c5fef6f96b4c2fbd400e6565b3b41f54aef`.
The continuing development branch is `v2-current`; its PR stays Draft for further
tests and formal verification. Acceptance of the archived source does not qualify
new commits or establish shuttle submission or physical-board operation.

## Download and verify

Download [the full successful-run archive](https://github.com/mcranny/protocol-emulator-asic/releases/download/v2-local-evidence-20261008/v2-local-acceptance-af573c5.tar.gz) and its
[SHA256SUMS](https://github.com/mcranny/protocol-emulator-asic/releases/download/v2-local-evidence-20261008/SHA256SUMS).
From a new directory:

```sh
shasum -a 256 -c SHA256SUMS
tar -xzf v2-local-acceptance-af573c5.tar.gz
python3 accepted/source/tools/check_v2.py accepted
```

The checker verifies input/artifact hashes and same-source identities before
checking exact suite identities, formal/mutation evidence, physical metrics,
precheck, routed-netlist identity and full-geometry rendering. This verifies
retained evidence; it does not rerun simulation or physical implementation.
The source snapshot includes pinned tools and dependencies for a fresh run.
Physical continuation also needs compatible PDK/tool installations and relocated
mounts recorded in the manifests; this archive is not a turnkey physical rerun.

The archive contains the unchanged `accepted/` bundle plus successful final
functional, gate-level, physical, precheck and render directories. Its top-level
`successful-run-files.json` hashes all 1,085 retained files. Failed attempts and
unrelated work directories are excluded. Original absolute paths and container
names remain in hash-bound provenance; they are historical metadata, not required
locations for checking the self-contained accepted bundle. No evidence was
redacted or assigned a new source identity.

## Relationship to the development source

[acceptance.json](acceptance.json) records results and the archive checksum.
[history-map.json](history-map.json) maps original implementation commits to the
restructured history and records work dates. The new documentation/evidence
index commit is dated when authored, rather than backdated.
[source-comparison.json](source-comparison.json) compares the accepted source
inventory with the development implementation before this index was added.

RTL, firmware, formal properties and final physical configuration retain their
accepted bytes. The published full-detail viewer improvements are preserved.
Abandoned SRAM code/tests and investigation journals are excluded. Documentation
and CI are reorganized, and an obsolete documentation reference in a tool
module docstring is corrected. Historical source still appears intact inside
the separately archived accepted snapshot because its hashes are evidence.
