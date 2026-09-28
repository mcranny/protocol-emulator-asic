# V2 validation and acceptance

The V2 implementation follows the executable [contract](v2-contract.md).
Acceptance requires all of the checks below on a single clean source revision,
with a complete evidence bundle. The released V1 top, its acceptance artifacts
and version tags remain separate from V2 validation.

## Checks implemented

- Python model, codec, independent peers and host tests run with the existing
  Python suite. UART tests cover both rates, asynchronous phases, baud mismatch,
  false starts, framing errors and break recovery.
- Three RTL differential suites compare per-cycle UART, instruction and FIFO
  boundary behavior against the Python model.
- Fourteen pin-driven suites exercise host safety, capture readout/noninterference,
  the UART fault/scenario demonstration,
  bounded firmware playback,
  sustained 115200 full duplex,
  16-byte full-duplex bursts at 1 Mbaud, both SPI roles in all modes and both
  I2C roles with independent peers, protocol errors and recovery, SPI/I2C fault
  scenarios, input spike filtering, and the custom pulse protocol. The same
  suite runs on a routed netlist.
- Formal induction proves the documented core safety properties with arbitrary
  instruction words. This does not establish complete protocol correctness.
- Mutation checks require actual assertion failures for dropped RX records,
  incorrect packed TX ordering, inverted wait edges, shifted capture timestamps
  unsafe open-drain output enabling, and overlapping ownership acceptance. Compile
  failures, missing results and skipped tests do not count as detections.
- [Capture](v2-capture.md) has standalone differential, model and induction
  tests plus integrated pin/host tests. Physical acceptance covers the combined
  engine, capture and input-filter design.

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
The 64-word firmware images fit the required protocol sketches; the combined I2C controller uses all 64 words and the target uses 62. A 128-word implementation has not been selected.

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
on nine instances. That result did not establish combined physical acceptance. A local trial
uses wider displacement and automatic wire-length repair instead of imposing
150 um on every signal. Final electrical, timing and layout checks remain
mandatory regardless of optimization settings.
No footprint, clock, memory depth or electrical limit has been relaxed.

## Functional contract and workflows

The v2 ISA encodings and cycle semantics are specified in the contract. The
custom pulse protocol uses only that ISA: one sync pulse, a gap, and four bits
encoded by 400/800 ns high times. All sixteen values are checked against an
independent decoder in the model; representative values loop back between the
two engines through pins. No protocol-specific hardware was added for this demo.

Input filtering requires three equal synchronized samples. Model, formal and
pin tests exercise both polarities and asynchronous pulses through 50 ns. The
I2C controller compensates for the selected filtering latency. Controller tests check START setup/hold, repeated START, STOP setup and SDA
setup in addition to SCL periods. Target tests check SDA setup at both rates, including an empty TX FIFO refilled by the host
while SCL is stretched. Error coverage includes partial SPI transfers in all
modes, underrun, I2C NACK/arbitration/stuck lines and nine-clock bus clear.
Analog rise/fall times and physical board operation are outside digital tests.

Core, capture, input-filter and compositional integration induction run locally.
The integration proof assumes the independently verified engine/capture interface
contracts; it is not a monolithic proof of arbitrary protocol firmware. The
ownership-overlap mutant must produce a real counterexample, just as the five
simulation mutants must produce assertion failures.

[Protocol fault scenarios](v2-scenarios.md) include UART framing faults, delayed
SPI responses and I2C NACKs. JSON/VCD export, exact replay, bounded firmware
playback, and host preparation/readout have model and pin-driven checks.

## Final acceptance

The combined capture/filter design requires full physical, precheck and
all fourteen routed-netlist suites on one clean source revision. Earlier
baseline evidence cannot establish acceptance of changed hardware. Current
placement/routing experiments preserve the footprint, clock, capacity and
all electrical constraints. Standard clock-wire spacing avoids the router's
repeated per-net removal of optional double-spacing rules. In a controlled
experiment using the same placed design, reducing global-route capacity
reservation from 30% to 20% reduced overflow from 4,371 to 54. The router
recommended 13%, which produced zero overflow on every layer and reduced wire
length from 3,690,669 to 3,096,676 um.

The full combined build at `2948770` then passed routing DRC, Magic DRC and LVS,
but failed with 49 slow-corner slew violations, 13 fanout violations and one
antenna violation. Detailed routing added diode loads after the last electrical
repair; one affected net grew from four loads to thirteen. Stronger slew repair
also increases local routing demand. The current settings reserve 5% of routing
capacity, use a 70% slew repair guard band, repair antenna effects earlier with a
30% ratio margin, and use wire jumpers for post-route antenna repair. These
settings preserve all signoff limits; they do not establish acceptance without
the full checked evidence bundle.

Run the complete local functional gate with the established tool environment:

```sh
python tools/validate_local.py --output build/v2-functional-01 --require-clean
```

The runner refuses an existing output directory, snapshots source and its hashes,
records tool versions, collects exact Python test identities, and runs Python,
V1/V2 synthesis and lint, all RTL suites, five induction proofs and six mutations.
It rejects source changes during the run. Omit `--require-clean` only for
explicitly marked development evidence. A dirty run cannot pass final acceptance.

`tools/check_v2.py` accepts an evidence bundle only when functional, physical,
precheck, gate-level and render stages identify the same clean revision and
source hashes. It requires exact suite identities, no failed/skipped tests,
zero physical violations, all-corner setup/hold and electrical checks, and
matching netlist/GDS hashes. Python results must match the freshly collected
suite, so a stale smaller test result cannot pass by meeting a numeric threshold.

## Full-resolution layout rendering

```sh
python -m pip install -r requirements-viewer.txt
python tools/render_gds.py /absolute/final/layout.gds --output build/v2-render-01
python tools/view_gds.py /absolute/final/layout.gds --render-dir build/v2-render-01
```

The SVG preserves every GDS polygon, path and instance on every layer, including
vias and minimum-size features. Hierarchical references remain vector references;
there is no size cutoff, layer omission, hierarchy-depth limit or geometric
simplification. An independent traversal of the SVG verifies expanded per-layer
polygon counts against the GDS inventory. The lightweight HTML viewer uses a local KLayout service for pan/zoom and
layer selection, with all layers initially enabled. Each settled view is rendered
from the original GDS at the requested coordinates and scale. The browser holds
a bounded canvas instead of loading the complete SVG into its DOM; the SVG
remains a separate download. Auto mode uses a labeled area-density overview for
subpixel features, then switches to literal geometry as you zoom in. Translucent
fills and thin outlines keep overlapping layers readable. Manual mode selection
retains direct access to literal geometry. The manifest records GDS and SVG hashes,
units, precision, bounds and inventory. See [the viewer guide](gds-viewer.md) for
setup, validation and memory limits. Zoom in to inspect individual wires; a whole-chip screen view necessarily has fewer
pixels than physical features. GDS remains authoritative, and connectivity is
checked by LVS rather than inferred from an image.

The final artifact must be rendered from the same GDS that passes precheck and
physical signoff. An older baseline render is useful only to validate the
renderer and must not be labeled as the current v2 layout.

## Recording downstream stages

`tools/v2_evidence.py record` wraps a real command and records its exit status,
clean source identity and artifacts. Output paths must be new; a previous XML
file cannot be reused. Use `--kind gatelevel --netlist PATH` with
`--artifact gatelevel.xml=NEW_XML_PATH`, or `--kind precheck --gds PATH` with
`--artifact precheck.xml=NEW_XML_PATH`. Arguments after `--` are the command to
execute. For example, the gate-level command is:

```sh
python tools/v2_evidence.py record --kind gatelevel --output build/v2-gl-stage \
  --netlist /absolute/final/netlist.v \
  --artifact gatelevel.xml=/absolute/build/v2-gl.xml -- \
  make -C test -f Makefile.v2pins GATES=yes PDK_ROOT=/absolute/pdk \
  GATE_LEVEL_NETLIST=/absolute/final/netlist.v \
  SIM_BUILD=/absolute/build/v2-gl-sim COCOTB_RESULTS_FILE=/absolute/build/v2-gl.xml
```

The render stage takes `--gds` and declares `layout.svg`, `render-manifest.json`
and `index.html` as artifacts, wrapping `tools/render_gds.py`. Precheck must run
all nine checks from the pinned Tiny Tapeout support-tools revision; a successful
command alone does not establish that its XML contains those checks.

For a shorter local gate-level run, `tools/validate_gatelevel.py` compiles the
same netlist once with Icarus and runs all fourteen pin suites in independent
processes. Each suite has a separate XML result and log. The raw cocotb XML
also lists the other collected suites as filtered skips. The runner validates
those identities and requires a real pass for each assigned suite across all
fourteen processes. A failed, skipped, duplicate or missing assigned suite
prevents creation of the combined `gatelevel.xml`.
Use a new output directory and retain its per-suite logs and `commands.json`:

```sh
python tools/validate_gatelevel.py --netlist /absolute/final/netlist.v \
  --pdk-root /absolute/pdk --output build/v2-gatelevel-01 --jobs 3
```

For acceptance, wrap this command with `tools/v2_evidence.py record`, declaring
the combined XML as `gatelevel.xml` and preserving the individual results and
logs as additional artifacts. The unchanged pinned PDK models require Icarus:
their sequential-cell delayed inputs depend on specify timing-check constructs
that Verilator 5.048 ignores. A single-cell capture test reproduces
that simulator incompatibility. The gate-level run is functional simulation;
extracted multi-corner STA remains the timing gate.

Once all stages finish, assemble and validate the evidence:

```sh
python tools/v2_evidence.py assemble --output build/v2-accepted \
  --functional build/v2-functional-01 --physical build/v2-full-01 \
  --gatelevel build/v2-gl-stage --precheck build/v2-precheck-stage \
  --render build/v2-render-stage
python tools/check_v2.py build/v2-accepted
```

Assembly verifies stage inventories and the prepared physical-source hashes,
copies the full source and artifacts, and writes `acceptance.json` only after
all acceptance checks pass. Retain full build directories and mutation logs
alongside the portable bundle for diagnosis. Every command above runs locally;
none publishes or pushes changes.
