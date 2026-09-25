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
- Ten pin-driven suites exercise host safety, capture readout/noninterference,
  the UART fault/scenario demonstration,
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

A clean baseline full build at `b6b534b` subsequently passed local routed
electrical checks, Magic DRC and LVS. Its functional netlist is byte-identical
to the earlier route that passed all seven baseline routed-netlist suites.
All nine Tiny Tapeout prechecks also passed for that baseline, including full
KLayout DRC. The later SPI firmware clock correction passes both independent
routed-netlist suites on this baseline netlist at both rates and all modes.

The first integrated capture route at `08cb243` failed detailed placement
after clock-tree insertion and hold repair. Utilization before hold repair was
79.8675%; repair inserted 7,336 delay cells, after which 274 instances could not
be legalized within the configured displacement limits. A wider-displacement
experiment passed hold legalization, but reached 93.1422% utilization before
global-route repair. Repair inserted 13,851 buffers, largely for 5,286 wires
exceeding the experimental 150 um wire-length setting; 4,513 instances then
failed legalization.

Sharing instruction-fetch and stopped-engine host readback at `52fd389`
reduced pinned synthesis area from 638,056.5156 to 618,348.9186 um2. With the
original displacement limits, post-clock-tree hold legalization still failed
on nine instances. Combined physical acceptance remains open. A local trial
uses wider displacement and automatic wire-length repair instead of imposing
150 um on every signal. Final electrical, timing and layout checks remain
mandatory regardless of optimization settings.
No footprint, clock, memory depth or electrical limit has been relaxed.

## Remaining acceptance work

The ISA remains unfrozen. Required work includes complete I2C timing/filtering
checks, wider protocol error/phase/abort cases, top-level isolation and burst
atomicity proofs, SPI/I2C scheduled faults, broader scenario execution,
the custom protocol demonstration and complete user workflows.
[UART fault scenarios](v2-scenarios.md) provide JSON/VCD export and deterministic
external-stimulus replay for the bounded demonstration.
[Bounded firmware playback](v2-replay.md) passes model and pin-driven checks;
its routed-netlist validation remains pending.

Before changed hardware is considered complete, full physical and representative
routed-netlist acceptance must pass. The final release also needs all required
suite identities and source/firmware/configuration/netlist provenance enforced
by the acceptance checker on one clean revision. Existing V1 checks cannot be
used to claim V2 acceptance.
