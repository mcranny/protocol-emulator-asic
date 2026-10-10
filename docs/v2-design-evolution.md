# V2 design evolution

V2 retains two 64-word engines, 16-entry TX/RX queues and 32 full-width capture
records within a 6x4 footprint. Physical experiments retain the 40 ns clock,
1.5 ns transition limit and 0.25 ns uncertainty. Results below describe the
specified experiments, not acceptance of every revision in this history.

## Programmable execution and observation

A shared instruction set carries protocol behavior in firmware. Triggered
capture observes synchronized inputs and engine activity without controlling
execution. Program fetch and stopped-engine readback share a read port.

## Independent behavioral verification

Independent wire decoders, fault/recovery scenarios and bounded replay check
externally observed behavior. Input filtering protects engine observations
while capture retains the unfiltered synchronized signal. Existing induction
proofs cover stated safety properties under explicit assumptions; they do not
prove every firmware protocol or physical-board behavior.

## Physical decisions from measured constraints

Combined capture exposed placement and routing pressure that the engine-only
baseline did not establish. Shared storage decoders reduced duplicated logic;
legalization, clock spacing and routing reservation then addressed measured
placement and routing limits without reducing capacity.

A matched routing experiment reduced reported overflow from 4,371 to 54 when
reservation changed from 30% to 20%. This was a routing measurement, not complete
physical acceptance. Later configuration changes require their own evidence.

Metal1 signal routing did not resolve closure: the retained September 27 run
ended after iteration 50 with 2,329 violations. The experiment does not justify
promoting Metal1 into the continuing design. Signal and clock routing use
Metal2-Metal4, with TopMetal1 excluded.

The isolated SRAM wrapper passed its functional test and reached zero router
and KLayout DRC, but failed power qualification, Magic DRC (22 errors) and LVS
(23,730 aggregate reported errors). Those aggregate errors are not independent
defect counts. SRAM area estimates did not establish a usable replacement.
The abandoned macro implementation and generated diagnostic reports are kept
outside this PR sequence.

A matched post-CTS hold-margin sweep reduced hold-delay cells from 7,440 to
6,963 and cell area from 802,083 to 793,912 square micrometers at 0.05 ns rather
than 0.10 ns. Flop count stayed 6,805. These are post-global-route measurements;
fresh fast/typical/slow reports and extracted timing remain separate gates.

The reusable outcome is a flow that checks provenance, records bounded routing
attempts, distinguishes stale metrics from fresh analysis, and preserves
checkpoints. Raw failed-run artifacts and chronological journals are excluded.

Historical measurements were checked against the retained validation and
closure records dated September 25 through October 6, 2026. They establish the
motivation for each decision; they do not establish causation for every later
improvement or acceptance of a changed source revision.

## Share RX reads and qualify the resulting candidate

After bounded repair of the previous placement plateaued, RX read sharing
selected the FIFO address before one read tree instead of reading head and next
entries in parallel. Delayed host consumption, wraparound and empty/lookahead
validity remain unchanged. Generic synthesis measured 27,386 to 25,990 cells
with all 6,871 storage flop cells retained. These generic counts are not mapped
area or a controlled attribution of the entire final routing improvement.

Fresh synthesis and placement, diode antenna repair, and a guarded fanout ECO
produced the final candidate. The archived run completed on October 8, 2026
with zero required physical/electrical violations, worst setup slack +18.370074 ns
and worst hold slack +0.052527 ns. It passed 560 Python tests, 14 gate-level suites,
nine precheck checks, existing RTL suites, five formal checks and six mutations.
The [evidence index](../evidence/v2-local-af573c5/README.md) binds those results
to the original source and provides the full successful-run archive.

The `v2-current` Draft PR remains the home for stronger behavioral tests,
broader formal properties and coverage review. Existing safety proofs retain
their stated assumptions. Successful local acceptance is a baseline for this
work, not a claim that verification is complete.
