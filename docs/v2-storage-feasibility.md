# V2 storage and repair-area investigation

Measured September 27, 2026 PDT / September 28 UTC. This is an initial
feasibility study, not a replacement implementation or physical acceptance.

## Paused routing experiment

`v2-closure-metal1` is Docker-paused, preserving the process and its in-memory
route. Its last completed routing iteration, 50, reported 2,329 violations;
iteration 51 was at 70% when paused. Pausing retains allocated memory and
depends on the Docker VM remaining alive. The existing snapshots and logs
remain on disk. No storage RTL or timing constraints were changed by this study.

Evidence is in `build/v2-feasibility/storage-study-20260928T041223Z/`:
`pause-state.json`, `sram-inventory.json`, `sram-validation.log`,
`placed-cells.json`, `cell-analysis.json`, and `hold-cones.json`, with the
inspection and analysis scripts. The route remains in
`build/v2-feasibility/closure-2948770/metal1-20260927T173409Z/`.

## Measured storage cost

The completed Metal2 baseline's final pre-DRT database contains 47,695 cells,
with 802,082.736 square micrometres of cell area. This is a completed baseline
checkpoint, not the paused experiment's in-memory placement or final GDS area.
Flip-flops were attributed using their Q-net names in that database.

| Storage | Contract capacity | Implemented flops | Flop area, µm² |
| --- | ---: | ---: | ---: |
| Two instruction memories | 2 × 64 × 24 bits | 3,072 | 150,493.594 |
| Capture records | 32 × 64 bits | 1,984 | 97,193.779 |
| Two TX FIFOs | 2 × 16 × 8 bits | 256 | 12,541.133 |
| Two RX FIFOs | 2 × 16 × 16 bits | 512 | 25,082.266 |
| Total | 5,888 logical bits / 736 bytes | 5,824 | 285,310.771 |

The 64-bit difference is consistent with two constant flag bits in each of
32 capture records. Bulk storage alone occupies 35.6% of baseline cell area.
These figures exclude read/write logic, validity bits, pointers, clock cells,
tie cells and repair cells. The older generic storage probe is not used as a
measurement of the current implementation.

The baseline contains 7,440 hold-delay cells (`dlygate4sd3_1`), each 16.3296 µm².
Tracing their outputs through combinational cells to sequential endpoints gives:

| Exclusive downstream storage destination | Hold-delay cells |
| --- | ---: |
| Capture records | 1,999 |
| Engine 0 instruction memory | 1,508 |
| Engine 1 instruction memory | 1,545 |
| Two RX memories | 650 |
| Two TX memories | 350 |
| Total exclusively reaching bulk-storage D pins | 6,052 |

This is structural association, not timing-path sensitization or proof that
these cells can be removed. Each cell is counted once by its complete endpoint
set. Mixed destinations are excluded from this table. The 5,052 cells associated
exclusively with capture or instructions occupy 82,497.139 µm², but that number
must not be added to a claimed SRAM saving without a replacement physical run.
New macro interfaces may themselves need repair.

## SRAM candidates

The pinned CMOS5L PDK links its SRAM directory to the SG13G2 SRAM library.
The following existing macros have LEF, GDS, Liberty, Verilog and CDL views:

| Application | Candidate | Macro area, µm² | Raw flop-minus-macro area, µm² |
| --- | --- | ---: | ---: |
| Both 64 × 24 instruction banks | Four `RM_IHPSG13_1P_64x16_c2`, two per engine | 60,961.792 | 89,531.802 |
| 32 × 64 capture records | One `RM_IHPSG13_1P_64x64_c2_bm_bist`, half its depth used | 50,489.133 | 46,704.646 |
| Combined | Five macros | 111,450.925 | 136,236.448 |

The combined raw difference is 17.0% of baseline cell area. It excludes wrappers,
macro halos, routing channels, power connections, and changed clock/repair costs.
It is not a forecast of final utilization or proof of closure. A 64×16 macro
measures 236.8 × 64.36 µm; the 64×64 macro measures 784.48 × 64.36 µm.
They fit individually inside the footprint, but a legal combined floorplan
with useful routing access has not been established.

A two-port `RM_IHPSG13_2P_64x32_c2` also exists at 52,620.882 µm² per instance.
It is larger than two single-port 64×16 instances (30,480.896 µm²), so it should
only be chosen if its independent ports solve an actual interface requirement.

Checks completed:

- Read all hierarchical GDS polygon/path/label layer pairs for the three macro
  types above. None were forbidden or unlisted under the pinned Tiny Tapeout
  CMOS5L layer rules. This is a layer screen, not geometric DRC or LVS.
- Ran the supplied PDK SRAM validation suite: **797 checks passed**. This checks
  supplied views and selected model behavior; it does not establish full-design
  equivalence, electrical characterization, physical DRC or LVS.
- Verified fast models are 1.32 V / **−55°C**, whereas V2 standard-cell fast
  timing is **−40°C**. Typical 1.20 V / 25°C and slow 1.08 V / 125°C match.
  The fast-corner mismatch is unresolved. Do not rename or silently alias the
  −55°C model to claim the required −40°C acceptance.
- Confirmed the 64×16 macro's `A_DLY` must be tied high. Integration must also
  tie mask/BIST controls correctly for the capture macro.

Capture is the first integration candidate: writes append records; host reads
are permitted when capture is frozen. Its synchronous SRAM read needs a wrapper
that meets the existing SPI response deadline, invalid-address zero behavior,
and immediate final-record read behavior. That timing has not yet been proved.
Instruction SRAM is more demanding: preserve start, jumps, waits and exact
cycle execution with synchronous reads, likely using next-address preparation
and prefetch. Adding an instruction bubble would violate existing behavior.

## Alternatives and ranking

1. **Capture SRAM feasibility prototype.** Resolve model-corner handling and
   macro DRC/LVS support, then validate the wrapper against existing capture and
   host tests, including final-write/read boundaries. Map it in a separate
   candidate and measure total area, clock load and repair demand.
2. **Instruction SRAM prototype.** Preserve both independent 64-word engines
   and all cycle semantics. The raw storage saving is larger, but synchronous
   fetch is a more intrusive change. Do not shrink the instruction memories.
3. **Standard-cell storage/read logic improvements.** Inspect bank locality and
   the two separate RX read selectors (`rx_data` and `rx_next`). The top-level
   consumer can potentially select an address before one shared read tree;
   simultaneous pop, response and empty/full cases need equivalence checks.
   Program and capture reads already use shared one-hot decoders, so merely
   introducing another shared decoder is not a new optimization.
4. **Latch-based storage as a separate experiment.** `dlhq_1` is 30.8448 µm²
   versus 48.9888 µm² for the mapped flip-flop. Replacing 5,824 storage elements
   gives a raw element-only difference of 105,670.656 µm², before write gating,
   read logic, timing and repair. A transparent latch is not a drop-in
   edge-triggered replacement: phase control and race/hold analysis are required.
   The library's `lgcp_1` and `slgcp_1` clock-gating cells are explicitly excluded
   from synthesis and P&R in the pinned flow, so gated-clock storage is not an
   immediately supported alternative.
5. **Repair reduction after storage architecture is chosen.** Revisit physical
   locality and short paths using actual min-path reports. Avoid assuming the
   hold-margin setting explains every delay cell, or removing required hold
   repair to obtain an attractive area figure.

All options preserve the 40 ns period, 1.5 ns transition limit, 6×4 footprint,
two-engine independence, FIFO capacities, 32 full capture records and full
timestamp behavior. No capacity cuts, timestamp truncation, production storage
replacement or new routing run were performed in this investigation.

## Reproduction

Run from the repository root. The inventory script uses gdstk from `.venv`;
the supplied validation suite also uses Icarus Verilog. `extract_cells.py`
runs under the pinned OpenROAD image with the baseline directory mounted at
`/baseline` and this study directory mounted at `/out`.

```sh
.venv/bin/python build/v2-feasibility/storage-study-20260928T041223Z/inspect_sram.py
.venv/bin/python build/deps/v2-pdk/ihp-sg13g2/libs.qa/sram/validate_sram.py
.venv/bin/python build/v2-feasibility/storage-study-20260928T041223Z/analyze_cells.py
.venv/bin/python build/v2-feasibility/storage-study-20260928T041223Z/analyze_hold_cones.py
docker inspect --format 'paused={{.State.Paused}} running={{.State.Running}} oom={{.State.OOMKilled}}' v2-closure-metal1
```

The PDK's [SRAM library documentation](https://ihp-open-pdk-docs.readthedocs.io/en/latest/contents/reference_libraries/sram.html)
describes the delivered macro views. Numerical conclusions above come from the
locally pinned files and retained baseline database, not from an unpinned
online library revision.
