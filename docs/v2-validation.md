# V2 development evidence

The V2 implementation is an executable architecture candidate. It is not a
completed V2 release. The released V1 top and acceptance artifacts remain
separate. The candidate contract is in [v2-contract.md](v2-contract.md).

## Checks implemented

- Python model, codec, independent peers and host tests run with the existing
  Python suite. UART tests cover both rates, asynchronous phases, baud mismatch,
  false starts, framing errors and break recovery.
- Three RTL differential suites compare per-cycle UART, instruction and FIFO
  boundary behavior against the Python model.
- Nine pin-driven suites exercise host safety, capture readout/noninterference,
  bounded firmware playback,
  sustained 115200 full duplex,
  16-byte full-duplex bursts at 1 Mbaud, both SPI roles in all modes and both
  I2C roles with independent peers. The same suite can run on a routed netlist.
- Formal induction proves the documented core safety properties with arbitrary
  instruction words. This does not establish complete protocol correctness.
- Mutation checks require actual assertion failures for dropped RX records,
  incorrect packed TX ordering, inverted wait edges, shifted capture timestamps
  and unsafe open-drain output enabling. Compile
  failures, missing results and skipped tests do not count as detections.
- The [capture candidate](v2-capture.md) has standalone differential, model and
  induction tests plus integrated pin/host tests. Combined physical acceptance
  is pending.

## Physical feasibility observations

The first dual-engine route failed with 11 slew and 15 fanout violations.
Clock-tree trunk fanout and long extracted output nets were the main causes.
An experiment using 100 um clock-buffer spacing and 150 um global-route repair
wire length passed the local route checker at the original 1.5 ns limit.

That experiment used an uncommitted source snapshot; its local manifest records
the exact files. It is development evidence, not evidence for a later revision.
The observed cell utilization was 72.8875% in the 6x4 footprint. Worst setup
slack across the three required corners was +19.4708 ns and worst hold slack
was +0.0905 ns. Configured slew, capacitance, fanout, routing DRC, antenna and
filtered signal-annotation violation counts were zero at every required corner.

This route does not include capture storage or establish full DRC, LVS,
precheck or release acceptance. Remaining area cannot be treated as proven
capacity for capture: the combined implementation must be routed again.
The 64-word firmware images fit the required protocol sketches; both combined
I2C roles use all 64 words. A 128-word implementation has not been selected.

## Remaining acceptance work

The ISA remains unfrozen. Required work includes complete I2C timing/filtering
checks, wider protocol error/phase/abort cases, top-level isolation and burst
atomicity proofs, scheduled faults, complete scenario export, external-stimulus
replay, the custom protocol demonstration and runnable user workflows.
[Bounded firmware playback](v2-replay.md) passes model and pin-driven checks;
its routed-netlist validation remains pending.

Before changed hardware is considered complete, full physical and representative
routed-netlist acceptance must pass. The final release also needs all required
suite identities and source/firmware/configuration/netlist provenance enforced
by the acceptance checker on one clean revision. Existing V1 checks cannot be
used to claim V2 acceptance.
