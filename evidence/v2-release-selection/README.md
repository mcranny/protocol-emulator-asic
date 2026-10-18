# V2 physical release selection — 18 October 2026

The repository owner selected the uploaded `447b3df` local TT checkpoint
recovery as the V2 physical release basis after hosted run `39087676085` timed
out. [selection.json](selection.json) records that decision, the unchanged
physical check results, layout hashes and correspondence to software revision
`881495c`. It is a release-selection record with explicit separate source
identities. Historical manifests are unchanged.

The [public recovery archive](https://evidence.mcranny.net/recovery-447b3df)
has SHA-256
`2d742a7604d573617cf5f0471e435858afdd9729505c9c9ebb275c075942b466`.
Its physical source is `447b3dfd0986d473c71afa28efafb3a5cff48da7`.
The package and functional evidence source is
`881495cb4d5a6270f5dd7625b1787d83f3059827`.

## Reproduce the source correspondence

For each revision, extract these files with their original relative names:
`src/host_spi.v`, `src/v2_project.v`, `src/v2_engine.v`, `src/v2_capture.v`,
`src/v2_input_filter.v`. Run the same Yosys executable in each extracted tree:

```sh
yosys -Q -T -p 'read_verilog src/host_spi.v src/v2_project.v src/v2_engine.v src/v2_capture.v src/v2_input_filter.v; hierarchy -check -top tt_um_mcranny_protocol_emulator_v2; proc; opt_clean; write_json design.json'
```

Remove only `src` members of `attributes` objects recursively from each JSON
document, then require exact equality of the entire documents. Keep all other
attributes, cell/port/net identities, parameters, memory descriptions and
connections. Hash the equal document using sorted keys and compact JSON
separators with UTF-8 encoding and no trailing newline. The tool identity,
canonical hash and raw input hashes are in `selection.json`.

Separately merge `src/config.json` with `physical/v2/overrides.json` at each
revision, removing only the `//` comment member; require exact equality. Require
byte equality of `physical/v2/info.yaml`. This compares the actual prepared V2
settings, including the existing fixed physical constraints. It does not erase
the recovery-specific thread, debug and additional DRC settings, which remain
explicitly recorded in the recovery object.

The existing `tools.check_physical.check` at `881495c` was rerun against the
selected final metrics/config and the original exact precheck/gate-level suite
identities. The bundle, GDS, netlist, checkpoint, recovery manifest and validation
summary hashes were rechecked. This comparison establishes source
correspondence, not a new RTL-to-GDS run or an independent sequential
equivalence proof against the mapped netlist.

## Evidence boundaries

- The hosted `881495c` job exceeded six hours. Its precheck, gate-level and
  acceptance jobs were skipped. It remains a timeout.
- The selected recovery used unchanged stock scripts and no checkpoint ECOs.
  The prior failed local route remains in the archive.
- The recovery's original functional count is 631; the later software gate
  passed 699 cases. Counts and source identities remain separate.
- The original reset supplement is from `c1c9be5`. Later reruns identify their
  own test revision and the same selected netlist hash.
- The successful CI reporting run recorded local evidence without rerunning
  physical checks. Its `release_acceptance: false` value remains unchanged.
- This selection does not change the common-source acceptance checker or claim
  it passed for `881495c`. The release explicitly carries separate physical and
  software identities with their correspondence evidence.
- Merge, tagging, publication and competition submission remain separate.
