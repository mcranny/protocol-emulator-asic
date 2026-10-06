# V2 physical closure: next steps and decision gates

Updated October 5, 2026, after restarting Docker Desktop and checking container
states, logs and saved artifacts. This document records the remaining work;
creating it does not launch a run or change the physical configuration.

Implementation began later on October 5. See the implementation record below
for fresh timing evidence; the original routing and acceptance gates still apply.

## Objective and recommended path

Determine whether V2 can achieve physical closure with its current functionality
and 32-record capture depth, using the measured 0.05 ns post-CTS hold-margin
candidate. Validate fresh all-corner timing, then perform one controlled
Metal2–Metal4 detailed-routing trial. If it succeeds, complete same-candidate
acceptance. If it fails, make an explicit capture-depth decision before another
architecture change.

Defer capture SRAM integration. The isolated macro wrapper failed its physical
qualification gate. A reduction in standard-cell area alone does not establish
routability or acceptance.

## Constraints to preserve

| Item | Required value or behavior |
| --- | --- |
| Clock period | 40 ns / 25 MHz |
| Maximum transition constraint | 1.5 ns |
| Clock uncertainty | 0.25 ns, unchanged for these experiments |
| Footprint | 6×4; die coordinates `[0, 0, 1289.28, 710.64]` µm |
| Signal routing for the next trial | Metal2–Metal4 |
| Clock routing for the next trial | Metal2–Metal4 |
| Upper-layer restriction | TopMetal1 remains excluded |
| Engines | Two independent engines, each with 64 × 24-bit instruction storage |
| FIFOs | Existing TX and RX capacities and behavior |
| Capture | 32 full-width records and full timestamp behavior unless a depth change is explicitly approved |

Use the pinned dependencies recorded in `tools/local_flow.py` and the experiment
manifests. Preserve the PDN geometry and other repair settings for the hold-margin
comparison. Do not use relaxed uncertainty, missing timing models, disabled
checks or stale metrics to obtain a passing result.

## Confirmed starting point

### Container and preservation status

- Docker Desktop was relaunched successfully. No experiment containers were
  running at the October 5 status check.
- `v2-closure-metal1` exited with code 255 after the computer restart;
  `paused=false`, `OOMKilled=false`. Its in-memory route cannot be resumed with
  `docker unpause`.
- Its 51 OpenDB snapshots, `drt_iter0.odb` through `drt_iter50.odb`, survive on
  disk, totaling approximately 3.19 GiB. Recovery from a snapshot would be a
  separate validated operation, not continuation of the old process.
- Last completed iteration: 50, with 2,329 violations. Iteration 51 was at 70%
  when paused. Last recorded container peak memory: approximately 2.29 GiB,
  with no recorded OOM events.
- Host free space at the status check: 769 GiB. Recheck before a new run.

Keep this run stopped and retain its artifacts as evidence.

### Hold-margin sweep results

The sweep container exited 0. All three variants completed post-global-routing
repair and timing; none ran detailed routing. The same pre-repair CTS checkpoint
and two-thread setting were used for the three variants. The post-GRT hold
margin remained 0.05 ns.

| Post-CTS hold margin | Hold-delay cells | Total cells | Cell area, µm² | Utilization |
| --- | ---: | ---: | ---: | ---: |
| 0.10 ns control | 7,440 | 47,695 | 802,083 | 88.8816% |
| 0.05 ns candidate | 6,963 | 47,187 | 793,912 | 87.9762% |
| 0.00 ns alternative | 6,897 | 47,140 | 793,029 | 87.8783% |

Flop count remained 6,805 in every variant. The 0.05 ns candidate removed 477
hold-delay cells and saved 8,171 µm², approximately 1.02% of control cell area.
Zero margin saved only another 883 µm² after subsequent repair. Prefer 0.05 ns
for the next trial; a 1% saving is not a guarantee of closure.

**Timing limitation:** the final mid-flow STA step refreshed the fast corner.
Some typical/slow corner entries in the cumulative metrics JSON were inherited
from earlier stages. A successful process exit and the combined JSON do not
prove fresh all-corner timing closure.

### SRAM qualification results

| Check | Result |
| --- | --- |
| Wrapper functional test | Passed all 64 addresses and 512 bytes |
| Initial physical attempt | Failed PDN generation |
| External Metal3/Metal4 ring attempt | Failed macro power connectivity |
| Detailed-routing signal DRC | 0 violations |
| Antenna | 0 violations |
| KLayout DRC | 0 violations |
| Magic DRC | 22 errors |
| Magic extraction | 29 illegal-overlap errors |
| LVS | 23,730 aggregate reported errors |
| Diagnostic continuation | Exited 2 |
| TT precheck | Exited 1 during environment startup; no layout-check result |

The diagnostic continuation started after the power failure to collect additional
evidence. It does not override that failure. The LVS aggregate is not a count of
independent physical defects; investigate extraction, model and connection
problems before interpreting individual mismatches.

The precheck container had networking disabled but attempted to fetch the pinned
Nix package source. After that failed, package evaluation reported
`sphinx-9.1.0 not supported for interpreter python3.11`. No completed precheck
report was produced. Separately, the SRAM fast model is characterized at −55°C,
while V2's standard-cell fast corner uses −40°C. That gap remains unresolved.

## 1. Prepare a reproducible 0.05 ns candidate

- [x] Verify the current RTL hashes against the hold-sweep manifest. Verify the
      completed checkpoint and its input/configuration provenance. If RTL has
      changed, rebuild the candidate rather than reusing an incompatible state.
- [x] Allocate a new output directory and unique container name. Do not overwrite
      the sweep, SRAM probe or old routing artifacts.
- [x] Use the saved `margin005` post-GRT checkpoint as the proposed starting
      point. Validate every path in the state file under the intended container
      mounts; preserve read-only access to referenced predecessor artifacts.
- [x] Resolve and record the following values in the experiment configuration:

  ```json
  {
    "PL_RESIZER_HOLD_SLACK_MARGIN": 0.05,
    "GRT_RESIZER_HOLD_SLACK_MARGIN": 0.05,
    "RT_MIN_LAYER": "Metal2",
    "RT_MAX_LAYER": "Metal4",
    "RT_CLOCK_MIN_LAYER": "Metal2",
    "RT_CLOCK_MAX_LAYER": "Metal4",
    "CLOCK_PERIOD": 40,
    "MAX_TRANSITION_CONSTRAINT": 1.5
  }
  ```

- [x] Preserve the remaining matched sweep settings, including repair margins,
      clock uncertainty, PDN and two-thread setting. Record the complete resolved
      configuration, tool image, PDK revision, TT revision and source hashes.
- [x] Keep diagnostic options such as `DRT_SAVE_SNAPSHOTS` in the experiment
      configuration, separate from permanent routing policy.

**Configuration trap:** `physical/v2/overrides.json` currently contains the
earlier Metal1 experiment's minimum signal layer. Do not accidentally regenerate
this candidate with Metal1 enabled. `tools/v2_candidate.py` also has an explicit
override allowlist; any later promotion of the hold-margin setting needs a
reviewed allowlist update and focused tests. This document does not promote or
apply those changes.

## 2. Establish fresh all-corner timing before detailed routing

- [x] Determine why the final mid-flow report refreshed only the fast corner.
      Inspect the executed STA commands and corner selection, not just the
      configured corner list.
- [x] Run explicit fresh analysis of the selected candidate at all three corners:
      `nom_fast_1p32V_m40C`, `nom_typ_1p20V_25C`, and `nom_slow_1p08V_125C`.
- [x] Retain separate per-corner reports and freshly generated metrics for setup,
      hold, transition, capacitance and fanout. Check clock propagation and
      parasitic annotation as well.
- [x] Establish which parasitics are estimated from global routing. Keep that
      limitation visible; detailed-route extraction is still required later.
- [x] Reject absent, stale or invalid metrics. Do not interpret a missing value
      as zero violations or rely on inherited cumulative metrics.

**Gate:** proceed only after the fresh reports show nonnegative required setup
and hold slack and zero required electrical violations at every corner. If a
corner fails, diagnose and repair under the fixed constraints, then repeat the
analysis. Do not advance by relaxing the contract.

## 3. Run one controlled detailed-routing trial

- [x] Recheck disk space and Docker memory headroom immediately before launch.
- [x] Start a detached container with retained logs and output, without automatic
      removal. `v2-closure-hold005` launched October 5 at 09:06 PDT.
- [x] Resume from the validated candidate checkpoint at the appropriate detailed
      routing step. Reuse global-route guides only if they match the candidate
      geometry and Metal2–Metal4 limits; regenerate them if either changed.
- [x] Enable DRT snapshots and verify that snapshots are actually being written.
- [ ] Record container current/peak memory, swap and OOM counters every 30 seconds;
      record process peak RSS when available. Periodically record snapshot growth
      and remaining disk space. Two threads are an experimental setting, not a
      proven peak-memory mitigation.
- [ ] Record DRT violations by iteration, layer, type and spatial concentration.
      Track signal-to-power conflicts explicitly.
- [ ] Compare against the original Metal2 run's 63-violation result and against
      the matched sweep's area/repair metrics. Keep the provenance of each
      comparison explicit.
- [ ] If violations persist without meaningful improvement, or resource pressure
      becomes material, preserve logs and snapshots before interrupting. Do not
      promise that an interrupt creates a checkpoint or keep an unproductive run
      going merely because the process remains alive.

**Gate:** zero final DRT violations and zero antenna violations, followed by
fresh extracted all-corner timing/electrical checks. A completed route is
development evidence until the acceptance work below passes.

The trial is active. Its supervisor samples resources every 30 seconds and
retains per-iteration ODB snapshots and DRC reports. It stops for less than
20 GiB free disk, container memory entering the reserved 1 GiB of VM headroom,
more than 512 MiB swap, or an OOM event. It also stops after iteration 50 if
the last ten completed iterations in the same route pass show less than 1%
improvement in the best violation count. These are experimental resource and
convergence policies, not physical acceptance criteria.

The first live observer read LibreLane's buffered per-step log and lagged
behind the router. A corrected observer was attached without restarting
OpenROAD. Its records are in `observer-20261005/` within the run directory;
use those progress records or the flushed `flow.log` for current status.

### Live observer

Open [the local live viewer](http://127.0.0.1:8766). The page refreshes every five
seconds and displays Docker state, iteration progress and completed counts,
memory history, swap/OOM counters, free disk, saved snapshots, DRC breakdowns,
spatial report bins and the latest log lines. Resource samples arrive every
30 seconds; the page flags samples older than 90 seconds. The viewer is
read-only and binds only to `127.0.0.1`. Closing it does not stop routing.

Start the viewer again from the repository directory if needed:

```sh
.venv/bin/python tools/v2_route_viewer.py \
  --run build/v2-feasibility/route-hold005-20261005 --port 8766
```

At the October 5, approximately 17:07 PDT check, iterations 0, 1 and 2 had
completed with 49,378, 33,654 and 32,623 violations respectively. Iteration 3
was at 20%. Three ODB snapshots were retained (174 MiB); peak container memory
was 2.89 GiB, with zero swap use and zero OOM events. The historical Metal2
comparison was 63 violations at iteration 49; this new trial has not reached
a comparable late iteration or its routing gate.

Per-iteration DRC report files can cap reported categories: iteration 2's
report contains 17,751 records versus the router's full total of 32,623.
Its layer/type/spatial breakdowns and 1,102 reported signal-to-power conflicts
describe those report records only. Do not interpret them as complete counts.

At the October 5, 17:15 PDT liveness check, OpenROAD used 30 CPU-seconds in a
15-second sample (both configured cores). CPU throttling, memory pressure,
swap and OOM counters showed no resource stall. The observer was fresh, and
iteration 3 advanced from 30% to 50% during the investigation.

The apparent eight-hour runtime included approximately 5 hours 56 minutes of
macOS sleep recorded across 44 sleep events. The Docker VM had accumulated
only about 2 hours 14 minutes of active uptime. Wall elapsed time therefore
substantially overstated active routing time. A temporary
`caffeinate -i docker wait v2-closure-hold005` guard now prevents idle system
sleep for the container's remaining lifetime; display sleep remains allowed.
The assertion was verified with `pmset -g assertions`. Explicit sleep or closing
the laptop lid can still suspend the machine.

- [Measured CPU activity](../build/v2-feasibility/route-hold005-20261005/health-check-20261005.json)
- [Host sleep evidence](../build/v2-feasibility/route-hold005-20261005/host-sleep-check-20261005.json)
- [Temporary idle-sleep guard](../build/v2-feasibility/route-hold005-20261005/idle-sleep-guard.json)

Monitoring commands, once the proposed container exists:

```sh
docker logs --follow --timestamps --tail 100 v2-closure-hold005
docker stats v2-closure-hold005
docker inspect --format 'status={{.State.Status}} exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' v2-closure-hold005
```

Ctrl+C exits the log/stats monitor without stopping the container. Docker pause
retains RAM only while the Docker VM remains alive; it is not reboot-safe
preservation.

## 4. Complete acceptance if the route succeeds

- [x] Repair the TT precheck launcher. Make its pinned dependencies available
      deterministically before starting the checks; do not silently fall back to
      an unrelated package set when a download is unavailable.
- [ ] Verify the precheck runtime and retain dependency/tool versions and its
      launch command. Re-run the SRAM probe precheck only as diagnostic evidence;
      it cannot qualify that probe while its other failures remain unresolved.
- [ ] Complete the chosen V2 candidate's full physical flow, including power
      connectivity, extraction, all-corner STA, DRC and LVS.
- [ ] Run all required TT precheck suites on the exact final GDS. Require the
      complete passing report, not merely a successful environment startup.
- [ ] Run the required functional and routed-netlist tests; update affected tests
      for any implementation change. Use the existing V2 acceptance machinery
      in `tools/check_v2.py` and `tools/v2_evidence.py`.
- [ ] Render and inspect the same final GDS and record GDS/netlist hashes.
- [ ] Assemble physical, precheck, functional, routed-netlist and rendered evidence
      for the same source/configuration revision. Prepare a reproducible clean
      acceptance revision; do not combine results from different candidates.
- [ ] Promote the selected physical overrides only with the appropriate tests
      and evidence. Preserve V1 release inputs and prior experiment artifacts.

**Done means:** the same V2 candidate passes every required acceptance check.
Neither a zero-DRC routing log nor a process exit code alone is sufficient.

## 5. Capture-depth decision if the 32-record candidate still fails

Do not silently reduce capture depth. Present the remaining violations, actual
resource use and the 0.05 ns trial's result, then obtain an explicit decision
between retaining 32 records and testing 16 records.

The proposed 16-record fallback preserves full record width, full timestamps,
both engines, instruction capacity and FIFOs. Relative to the measured current
capture implementation, it would remove approximately 992 storage flops, or
48,597 µm² of flop area alone. Selector, clock and repair savings are additional
unknowns that must be measured; do not extrapolate a guaranteed utilization or
closure result from the raw flop count.

If 16 records are approved:

- [ ] Update the V2 contract and capture-capacity documentation explicitly.
- [ ] Update RTL and host-visible count, capacity, truncation and read-address
      behavior consistently. Define reads beyond the new capacity.
- [ ] Review host tooling, replay/scenario expectations and tests for assumptions
      that exactly 32 records are available.
- [ ] Test capacity boundaries, final-record capture, truncation, invalid reads,
      trigger behavior and timestamp preservation. Run affected formal checks.
- [ ] Build from synthesis in a fresh candidate; a checkpoint containing the old
      storage implementation cannot establish the new design's result.
- [ ] Measure flop count, hold-delay cells, total repair cells, cell area and
      utilization, then repeat the same routing and acceptance gates.

If 32 records must remain, evaluate a separately scoped storage/physical redesign
using the failure evidence. Do not weaken the fixed timing requirements.

## 6. SRAM work deferred until separately prioritized

Before any capture SRAM integration, the isolated wrapper must pass:

- [ ] A legal power hookup for `VDD!`, `VDDARRAY!` and `VSS!`, with connectivity
      demonstrated under the allowed Metal1–Metal4 stack and TT boundary rules.
- [ ] Signal routing with the macro's real LEF obstructions, including pin access
      and signal-to-power clearance at its edges.
- [ ] Magic DRC and extraction without the current overlap failures.
- [ ] LVS against correctly interpreted macro netlists, power pins and devices.
- [ ] The complete TT precheck on the same wrapper GDS.
- [ ] A defensible resolution of the −55°C SRAM / −40°C standard-cell fast-model
      mismatch; do not relabel a model to claim characterization it lacks.

Only after qualification should a capture wrapper be implemented and validated
for synchronous-read latency, SPI response deadlines, invalid reads, final-write
boundaries and unchanged capture semantics. Then measure the integrated design
on a fresh route. Do not treat the current probe as qualified because one DRC
engine and the signal router reported zero violations.

## Evidence and implementation references

### October 5 implementation record

Steps 1 and 2 are complete for the unchanged 32-record candidate. The retained
container `v2-closure-hold005-timing-c` exited 0; its fresh gate report has
`passed: true` and no issues at any corner.

| Fresh corner | Worst setup slack, ns | Worst hold slack, ns | Slew / capacitance / fanout violations |
| --- | ---: | ---: | --- |
| `nom_fast_1p32V_m40C` | 22.2352 | 0.0446066 | 0 / 0 / 0 |
| `nom_typ_1p20V_25C` | 21.4477 | 0.199226 | 0 / 0 / 0 |
| `nom_slow_1p08V_125C` | 20.1227 | 0.470544 | 0 / 0 / 0 |

All setup/hold violation counts, including register-to-register counts, are
zero. Every corner passed the propagated-clock and global-route checks.
The annotation audit identified 143 unannotated drivers per corner; every one
was unconnected according to ODB pin connectivity. There were zero connected
unannotated drivers, zero unknown drivers and zero partially unannotated drivers.

These are **estimated global-route parasitics**, not detailed-route extraction.
That timing-only run did not launch detailed routing. The subsequent step 3
trial is described above. Final physical closure and release acceptance remain
unproven.

The runner has 27 focused tests covering incompatible provenance, path escape,
missing and invalid metrics, corner identity, annotation connectivity and fresh
output handling. The complete Python suite passed: **506 tests**. No RTL changed,
so functional RTL and formal reruns were not part of this tooling-only change.

Two earlier implementation attempts remain preserved: the first failed during
Python startup; the second generated timing but failed report collection due
to a step-directory naming mismatch. Only the corrected `-c` run establishes
the passing gate reported here.

- [Passing timing gate and report hashes](../build/v2-feasibility/closure-hold005-20261005-c/timing-gate.json)
- [Input provenance and launch command](../build/v2-feasibility/closure-hold005-20261005-c/manifest.json)
- [Prepared complete experiment configuration](../build/v2-feasibility/closure-hold005-20261005-c/config.json)
- [Fresh per-corner report directories](../build/v2-feasibility/closure-hold005-20261005-c/timing/)
- [Retained timing log](../build/v2-feasibility/closure-hold005-20261005-c/flow.log)
- [Memory, swap, OOM and disk samples](../build/v2-feasibility/closure-hold005-20261005-c/resources.jsonl)
- [Runtime versions](../build/v2-feasibility/closure-hold005-20261005-c/versions.json)
- [Closure timing runner](../tools/v2_closure.py)
- [Closure regression tests](../tests/test_v2_closure.py)

### Routing and precheck continuation

`tools/v2_route_trial.py` verifies the fresh timing reports and hashes, current
RTL, pinned dependencies, unchanged checkpoint views/configuration and ODB
geometry before routing. It retains logs, resource samples, snapshots and DRC
reports in a fresh output directory. The routing and observer tools have focused
tests for stale evidence, incomplete reports, convergence parsing and resource
limits. The live viewer reads these files and independently checks Docker state.

`tools/v2_precheck.py` uses the cached precheck image by immutable image ID,
verifies that it derives from the pinned LibreLane base, checks the complete
Python package tree against recorded hashes and validates exact TT package
versions. It invokes the cached binaries directly with networking disabled;
there is no Nix package-set evaluation or download fallback. The corrected
runtime-only probe exited 0 with Python 3.11.14, KLayout CLI 0.30.4, Magic
8.3.568 and the exact pinned Python packages, including KLayout Python 0.28.17.post1.
Its `runtime_passed: true` is deliberately separate from `precheck_passed: false`.
The final candidate GDS and LEF do not exist yet, so no full layout precheck is
claimed. Export the cached image and package tree when moving to another machine.

- [Routing launch manifest](../build/v2-feasibility/route-hold005-20261005/manifest.json)
- [Current route status](../build/v2-feasibility/route-hold005-20261005/route-status.json)
- [Corrected observer progress](../build/v2-feasibility/route-hold005-20261005/observer-20261005/route-progress.json)
- [Corrected observer resource samples](../build/v2-feasibility/route-hold005-20261005/observer-20261005/resources.jsonl)
- [Corrected observer snapshot inventory](../build/v2-feasibility/route-hold005-20261005/observer-20261005/snapshots.json)
- [Offline precheck runtime result](../build/v2-feasibility/precheck-offline-20261005-b/stage.json)
- [Offline precheck runtime versions](../build/v2-feasibility/precheck-offline-20261005-b/versions.json)
- [Live viewer server](../tools/v2_route_viewer.py)
- [Offline precheck launcher](../tools/v2_precheck.py)

### Closure runner

`tools/v2_closure.py` implements candidate preparation and the fresh all-corner
timing gate. It checks the sweep input hashes, current and predecessor RTL,
pinned dependency revisions, completed sweep status, resolved configuration and
the relationship between the final STA checkpoint and repaired post-GRT views.
It validates referenced artifact paths under the original mounts, mounts the
predecessor inputs read-only, clears inherited metrics and records input hashes.
Output directories and container names must be unused.

The pinned `OpenROAD.STAMidPNR` calls `openroad/sta/corner.tcl`, whose reporting
loop selects the first loaded corner and then breaks. Thus a multi-corner
`PNR_CORNERS` setting does not produce fresh reports at every corner in this step.
The closure runner uses three separate processes, each with one PNR corner,
and assesses only that process's raw `or_metrics_out.json`.

`physical/v2/closure/audit.tcl` requires saved global routes, one timing corner
and a propagated 40 ns clock. `tools/v2_closure_annotation.py` checks unannotated
drivers against ODB pin connectivity. A post-route filter based on detailed-wire
presence would be inappropriate for this global-route checkpoint. Unknown
drivers, connected unannotated drivers and partial annotation fail the gate.
Each process retains its timing, electrical, clock and annotation reports.

Reproduce in a **new** output directory and container name:

```sh
.venv/bin/python tools/v2_closure.py \
  --sweep build/v2-feasibility/hold-margin-20260928 \
  --predecessor build/v2-signoff/full-2948770 \
  --pdk-root build/deps/v2-pdk \
  --tt-root build/deps/v2-tt \
  --output build/v2-feasibility/closure-hold005-NEW \
  --name v2-closure-hold005-timing-NEW \
  --launch
```

Omit `--launch` to prepare inputs and record the exact container command without
starting analysis. With `--launch`, the command starts detached; its return does
not establish a passing timing gate. Inspect `timing-gate.json` and the container
exit status after completion. `flow.log` retains the analysis log on the host;
`resources.jsonl` samples cgroup memory, swap, OOM counters and free disk space
every 30 seconds and at startup/completion. LibreLane also retains per-process
resource reports. This runner stops after timing; it never launches DRT.

The original candidate generator and permanent physical override policy remain
unchanged by this implementation. Snapshot saving is set only in the prepared
experiment configuration. This preserves the 32-record RTL and matched repair,
PDN and timing settings.

These build artifacts are local and may be ignored by Git. Archive or export
them explicitly when handing the work to another machine; a source commit does
not necessarily include them.

- [October 5 Docker inspection](../build/v2-feasibility/status-check-20261005/docker-inspect.json)
- [Recovered TT precheck log](../build/v2-feasibility/status-check-20261005/v2-sram-probe-precheck.log)
- [Metal1 snapshot inventory](../build/v2-feasibility/status-check-20261005/snapshot-inventory.json)
- [Hold-sweep provenance](../build/v2-feasibility/hold-margin-20260928/manifest.json)
- [Hold-sweep completion/resource records](../build/v2-feasibility/hold-margin-20260928/sweep-status.json)
- [0.05 ns experiment configuration](../build/v2-feasibility/hold-margin-20260928/margin005.json)
- [0.05 ns post-GRT state](../build/v2-feasibility/hold-margin-20260928/runs/margin005/10-openroad-stamidpnr-3/state_out.json)
- [0.05 ns cumulative metrics; observe the timing limitation above](../build/v2-feasibility/hold-margin-20260928/runs/margin005/final/metrics.json)
- [SRAM diagnostic errors](../build/v2-feasibility/sram-probe-20260928-c/runs/diagnostics/error.log)
- [SRAM diagnostic metrics](../build/v2-feasibility/sram-probe-20260928-c/runs/diagnostics/final/metrics.json)
- [SRAM wrapper preparation](../physical/sram_probe/prepare.py)
- [SRAM wrapper functional test](../tests/test_sram_probe.py)
- [V2 candidate preparation](../tools/v2_candidate.py)
- [V2 acceptance checks](../tools/check_v2.py)
- [V2 evidence collection](../tools/v2_evidence.py)
- [Earlier storage feasibility study](v2-storage-feasibility.md)

The earlier storage study is historical: its statement that the Metal1 container
is paused predates the reboot. The container status and decision gates in this
document supersede that operational status.

## Overnight restart: October 5, 22:53 PDT

The user requested termination and a corrected retry after slow convergence,
then requested assessment of additional cores and simultaneous runs.

The original `v2-closure-hold005` container is stopped (exit 130, no OOM).
Iteration 51 completed with **210 violations**, unchanged through iterations
47–51. Iteration 51 consumed 67m26s elapsed / 104m16s CPU. Iteration 52 was
interrupted. This was an active routing plateau, not a dead process or a fresh
sleep interruption. All 52 completed ODB snapshots (0–51), DRC reports, logs,
and resource histories remain host-mounted. The pre-stop inventory contains
109 hashed artifacts. Interrupting did not create a completed routed state or
save the unfinished iteration's in-memory search state.

Evidence: `build/v2-feasibility/route-hold005-20261005/stop-20261005/`, especially
`inventory-before.json` and `inspect-after.json`. The old container is retained.

### Diagnosis and bounded search change

The 210 reported records comprise 147 shorts and 63 spacing violations:
137 on Metal2, 56 on Metal3, and 17 on Metal4. They occupy three 100 µm bins
at (300,100), (400,100), and (700,100), with 40, 108, and 62 records respectively.
Only one record is a signal-to-VGND short. This does not support changing the
PDN as the primary remedy. The original global-route report had zero overflow;
that coarse report did not predict the local detailed-route conflicts.

Inspection of pinned OpenROAD revision
`dcf36133a369abc8f3c5e5738cd4d82e4903c0e0` found that its flow remains in general
optimization above 100 violations. Later iterations substantially increase maze
search effort. The snapshot debug branch only writes the database; disabling
snapshots was not justified by this inspection. The recorded seed 42 also used
`or_k=0`, which performs zero net-order swaps. A seed-only retry would therefore
not change this ordering. Source excerpts are retained in the stop directory.

The retry uses seed 43 with **one adjacent net-order swap per candidate net**
(`-or_k 1`), and six OpenROAD threads. This is a controlled attempt to escape the
observed ordering/local-congestion plateau, not proof the physical conflicts are
fixed. The pinned LibreLane DRT script's SHA-256 is checked before applying the
two search arguments; both original and executed script hashes are recorded.
The same validated pre-DRT checkpoint and all-corner timing gate are used.
Placement, PDN, RTL, capture depth, timing constraints, and routing layers remain
unchanged. Extracted timing and complete acceptance still must pass on the
resulting route.

### Resource decision

Docker reports **8 CPUs and 7.748 GiB RAM**. The previous two-thread run's measured
peak cgroup memory was **3.000 GiB**, with zero swap/OOM. The chosen configuration
is **one six-thread run**, leaving two CPUs available. A live startup sample
reported **597.24% CPU**, confirming approximately six cores are being used.
This establishes utilization, not a measured threefold wall-time speedup; later
localized repair may offer less parallel work.

Two simultaneous runs at the old peak would consume about 6 GiB before extra
worker memory and VM overhead. They might fit, but available headroom is too small
to prefer this overnight without measuring the new peak first. Eight threads
are available but unbenchmarked; six is the initial balance. No concurrent
routing container was launched and Docker's VM allocation was not changed.

### Active run and monitoring

- Container: `v2-closure-hold005-retry1`, launched **22:53:16 PDT**.
- Directory: `build/v2-feasibility/route-hold005-20261005-retry1`.
- Live viewer: **http://127.0.0.1:8766/**. The existing viewer was restarted against
  this directory; its API and browser show the new container and six threads.
- `restart-assessment.json` records diagnosis, resources, and experimental delta.
- `manifest.json`, `route-search-sha256.json`, and the step `COMMANDS` record
  provenance and the actual six-thread/seed43/swaps1 invocation.
- `caffeinate -i docker wait v2-closure-hold005-retry1` is active and was verified
  with `pmset -g assertions`. It ends with this container and allows display sleep.
- The supervisor samples every 30 seconds from the flushed flow log. It now also
  retains CPU statistics/pressure alongside memory/swap/disk data.
- Stop at five unchanged nonzero completed iterations once iteration >=40,
  or the existing <1% improvement over ten iterations once iteration >=50.
  Different antenna passes are never combined for this decision.
- Absolute runtime cap: **14 hours**, approximately 12:53 PDT October 6.
  Existing low-disk, memory-headroom, swap, and OOM stops remain active.
- On stop or failure, retain the container, snapshots, logs and reports for review.
  Do not automatically repeat the identical search or claim route acceptance.

```sh
docker inspect v2-closure-hold005-retry1 --format '{{json .State}}'
docker stats v2-closure-hold005-retry1
tail -f build/v2-feasibility/route-hold005-20261005-retry1/flow.log
```

If the viewer needs restarting, use the retry directory rather than the old run:

```sh
.venv/bin/python tools/v2_route_viewer.py \
  --run build/v2-feasibility/route-hold005-20261005-retry1 --port 8766
```

Validation: **540 tests passed**, including new tests for the shorter plateau
stop, pass boundaries, progress/zero violations, and pinned-script search changes.
Startup verified the actual `-threads 6`, `-or_seed 43`, `-or_k 1` command and
geometry audit. No new completed routing iteration or closure result existed
at this startup handoff.

## Snapshot-based incremental repair: October 6, 10:25 PDT

The six-thread overnight retry stopped at 04:31:59 PDT after iterations 50–54
all completed at 131 violations. Its final report contains 105 shorts and 26
spacing violations. Of these, 61 fall in the viewer bin (300,100), 69 in
(400,100), and one in (300,0). These display bins are not OpenROAD worker tiles.
There are zero reported signal-to-power conflicts. All recorded iterations
remained in the optimization strategy within detailed routing.

At the user's request to resume only routing repair, a fresh, independent
whole-layout DRC audit loaded `drt_iter54.odb` in the pinned OpenROAD image.
It exited 0 and exactly reproduced the 131-record count and layer/type/spatial
summary. The source snapshot and old run remain read-only and retained.

**Strategy limitation:** this pinned router has no normal Tcl option to force
its named `guides tiles` / `stubborn tiles` strategies above the compiled
100-marker threshold. This experiment does not claim to override that threshold.
It uses the router's existing developer single-step interface to restart from
existing wires, followed only by DRC-driven repair passes. The log can still
say `optimization`; this is a strategy inside detailed routing.

- Container: `v2-closure-hold005-resume`, started 10:25:06 PDT.
- Output: `build/v2-feasibility/route-hold005-20261006-resume`.
- Viewer: http://127.0.0.1:8766/ , repointed to this new diagnostic run.
- Script: `tools/v2_route_resume.py`; original snapshot hash and all executed
  Tcl batch scripts/commands are retained with the run.
- Each batch initializes with INCR rip-up mode (3), then three DRC-only steps
  (mode 0). No ALL rip-up mode (1) is scheduled. Repair clip sizes vary across
  batches (9, 11, 13, 7 routing grid cells), with maze effort bounded at 8.
- Only an independently checked improvement becomes the input to a later batch;
  non-improving outputs remain available as evidence.
- Each completed batch writes an ODB then checks the complete layout without a
  bounding box, fixed-net filtering, or marker deletion. Snapshot files represent
  completed batches, not every internal iteration in the live log.
- Six threads, active idle-sleep guard, 30-second resource monitoring, existing
  memory/swap/disk limits. Maximum eight batches / two hours; stop after three
  batches without improvement in the independent whole-layout DRC count.
- No placement, PDN generation, synthesis, cell resizing, or architecture change
  is scheduled. Metal2–Metal4 limits are explicitly set. Existing 40 ns / 1.5 ns /
  0.25 ns and 32-record constraints remain the acceptance requirements.
- This isolated diagnostic does not automatically run extracted timing, antenna
  repair, signoff DRC/LVS, GL, or TT precheck. Even zero routing DRC is not release
  acceptance. A successful output must enter those gates with fresh provenance.

The audit container `v2-closure-hold005-resume-audit` and its command, Docker
inspection and full log are retained. Two new tests check that the generated
schedule never selects full rip-up and always checks the whole layout.

Validation after implementation: **542 tests passed**. The browser and API were
checked against `v2-closure-hold005-resume`; the viewer identifies incremental
repair from the 131-violation snapshot. It shows the independent baseline spatial
report until a completed repair batch produces a newer whole-layout report.
