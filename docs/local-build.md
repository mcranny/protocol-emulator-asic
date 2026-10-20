# Local physical builds

The pinned LibreLane container supports native ARM64 on Apple Silicon; Linux
AMD64 is also available. Docker must be running. Local optimization does not
need GitHub Actions, and builds run with container networking disabled after
dependencies have been fetched.

## This machine

Recorded 19 October 2026. The Python unit tests run in the Windows virtual
environment at `.venv`. Simulation, formal checks, and the PDK run in WSL,
because the SRAM library is a symlink and does not survive an NTFS checkout.

- WSL 3.0.3.0, kernel 6.18.54.1-1, WSLg 1.0.79. Distro Ubuntu-24.04, WSL version 2.
- Ubuntu Python 3.12.3 and GNU make 4.3. Ubuntu was installed as root, so the
  Linux home directory is `/root`.
- Icarus Verilog 12.0 (stable), Verilator 5.020 (Debian 5.020-1), Yosys 0.33
  (git sha1 2584903a060).
- cocotb 1.9.2 and pytest 8.3.3 live in `/opt/eda`. Ubuntu 24.04 blocks
  `pip install --user`.
- Docker Desktop 4.96.0, engine 29.9.1, WSL2 backend. `docker run` from Ubuntu
  uses Docker Desktop's WSL integration.

The PDK checkout is `/root/pdk/ihp-open-pdk` at
`2bbec755dc67ca3db0261c3d6163e15735d66710`, the same commit as the recipe
below. `ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram` is a symlink to
`../../ihp-sg13g2/libs.ref/sg13g2_sram`. The 256x32 LEF `SIZE` is
`416.64 BY 118.78`. Supply-column positions read from that LEF are in
`physical/v3/pdn-columns.txt`. The column spacing is not one constant pitch.

Windows has no GNU make. `python tools/make_entry.py` runs the same pytest
command as `make test`. The V3 cocotb suites (`test/Makefile.v3*`) and
proofs (`formal/v3_*.ys`) run in WSL from the repository root. For example,
`make -C test -f Makefile.v3progmem` runs the program memory cocotb tests, and
`yosys -q formal/v3_progmem.ys` runs its proof. To simulate against the PDK
macro model, pass `DEFINES=-DFUNCTIONAL` and a `VERILOG_SOURCES` list of the
macro file, `RM_IHPSG13_1P_core_behavioral_bm_bist.v` (which holds the behavioral
core the macro model instantiates), and `../src/v3_progmem.v`. LibreLane is the
pinned container in the recipe below, not a package installed on the host.

## One-time setup

Use new, dedicated dependency directories. Preserve these exact revisions:

```sh
docker pull ghcr.io/librelane/librelane@sha256:d109140b8f17fc54f4fca998beb8124f4949404ec52e339eebd2250854a18b5a
git clone --filter=blob:none --no-checkout https://github.com/IHP-GmbH/IHP-Open-PDK.git /tmp/protocol-pdk
git -C /tmp/protocol-pdk sparse-checkout set ihp-sg13cmos5l ihp-sg13g2
git -C /tmp/protocol-pdk checkout 2bbec755dc67ca3db0261c3d6163e15735d66710
git clone https://github.com/TinyTapeout/tt-support-tools.git /tmp/protocol-tt
git -C /tmp/protocol-tt checkout f6bf5c587fba4a4a8abd4c0a03234fccfbf6e61e
```

Keep **both PDK directories**: CMOS5L contains symlinks to shared SG13G2 files,
including the extraction rules. A CMOS5L-only sparse checkout cannot build.
The runner rejects modified tracked dependency files or incorrect revisions.

## Run

From the repository, using Python 3.11+ and a new output directory each time:

```sh
python3 tools/local_flow.py \
  --pdk-root /tmp/protocol-pdk --tt-root /tmp/protocol-tt \
  --output /tmp/protocol-route-01 --stage route --threads 4
```

`route` runs synthesis, placement, clock tree, routing, extraction, and final
STA, stopping before the expensive layout verification steps. It is an
optimization checkpoint, **not** a passing release gate. The runner additionally
checks routed electrical, timing, annotation, antenna, and routing-DRC metrics;
missing or violating metrics cause exit code 2 even when LibreLane exits zero.
Recheck an existing output without rebuilding with:

```sh
python3 tools/local_flow.py --check-only --output /tmp/protocol-route-01
```
Use `--stage full` with a fresh output directory for the complete Classic GDS
flow, including its enabled DRC/LVS/checker steps. Neither mode runs Tiny
Tapeout precheck or this repository's gate-level and functional regressions.

The helper copies only `info.yaml`, physical configuration, and Verilog inputs
to an isolated workspace. Original sources and dependencies are not changed;
existing output directories are never overwritten. It records the source
commit, dirty-state flag, exact input hashes, image identity/architecture,
dependency revisions, runner hash, stage, and exit status in
`local-evidence.json`. Tool versions and console output are in `flow.log`.
The generated user configuration matches the pinned support-tools recipe;
an exact-match regression test covers the V1 mapping.

Keep the output directory: it contains intermediate databases, routed netlist,
SPEF, STA reports, and reproducible failure diagnostics. The in-container
`/work` and `/pdk` paths are mount paths, not host paths. Logs can be watched in
another terminal with `tail -f /tmp/protocol-route-01/flow.log`.

## Limits

Local ARM64 results need not be bit-identical to Linux AMD64 CI. Final release
acceptance still requires all mandatory tests and physical checks on one
identified source revision; see [validation](validation.md). A shortened run,
a repaired intermediate database, or positive setup slack is not signoff.
Temporary directories may be removed by the operating system: use persistent
storage for evidence that must survive until release.
