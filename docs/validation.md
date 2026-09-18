# Validation record

## Initial scaffold

Source: `fe0a027824e66360c6543fef5b501daa13a169be`.

- Local Icarus/cocotb passthrough test: passed.
- [RTL CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810572): passed.
- [Documentation CI](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810548): passed.
- [Physical flow](https://github.com/mcranny/protocol-emulator-asic/actions/runs/35389810547): pending at the start of V1 work. Area, utilization, routed timing, precheck, and gate-level acceptance are not established yet.

## Reproducibility

Direct workflow actions are pinned. Upstream composite actions still resolve
some nested actions and dependencies dynamically. Record PDK metadata and build
logs from each physical run; direct pins are not a fully hermetic toolchain.

V1 cannot be tagged until the physical flow and functional acceptance described
in `v1-contract.md` pass on the release source commit.
