# V2 Tiny Tapeout qualification

## Current status and fixed configuration

The stock CMOS5L flow has not yet qualified this candidate. Historical local
closure at `af573c5` retains its original source identity and does not establish
submission compatibility for a new revision. A stock-flow violation is a
submission-level blocker; keep its failing artifacts and identify the required
repair before proposing a separately reviewed change.

The `447b3df` checkpoint recovery and its independent checks have passed;
the [recovery record](#validated-checkpoint-recovery) describes that evidence.
Final clean-candidate qualification remains pending.

Keep 6×4 tiles, a 40 ns clock, 1.5 ns maximum transition, 0.25 ns clock
uncertainty, and 32 capture records. The anticipated 8×4 template is deferred;
this qualification includes no migration or re-hardening for it.

## Repair provenance

Source inspection on 2026-10-10 compared the local implementation with
TinyTapeout/tt-gds-action `3412659307918422f3f0727917cf9b499aaca588`
(`ihp-cmos5l` branch), support tools
`f6bf5c587fba4a4a8abd4c0a03234fccfbf6e61e`, and LibreLane 3.1.0.dev3.
The action branch was re-queried on that date and still named this revision.
Its CMOS5L installer pins IHP-Open-PDK
`2bbec755dc67ca3db0261c3d6163e15735d66710`. That PDK's standard-cell
configuration supplies the 0.25 ns uncertainty; check the resolved value.

| Operation | Execution, inputs, and configuration | Effect on layout | Stock-flow comparison |
| --- | --- | --- | --- |
| Fresh-route | `tools/v2_fresh_route.py` prepares RTL, base config plus V2 overrides and fixed constraints. It runs through `STAMidPNR-3`, audits each corner separately, then explicitly resumes detailed routing and extraction from a hashed checkpoint. `tools/v2_route_flow.py` substitutes the detailed-routing script. | The local search uses seed 43 and one swap per net; routing topology may differ. Staging and corner audits also differ from a single Classic invocation. | Stock TT calls `tt_tool.py --create-user-config --ihp` and `--harden --ihp` from RTL. The pinned unmodified routing script uses seed 42. It does not call the staged runner. Equivalent acceptable closure remains an executable qualification question. |
| Connected-diode antenna repair | `v2_antenna_repair.py` verifies a routed checkpoint whose only remaining gate failure is antenna. `v2_route_flow.antenna_script` enters the repair loop and injects `set_power_nets.tcl` plus `set_global_connections` after diode insertion, before rerouting. | Connects newly inserted diode power pins and repairs the existing route. | The pinned LibreLane `drt.tcl` has antenna insertion and rerouting, but lacks this explicit reconnection call. Script inspection alone cannot establish whether the underlying router connects all inserted cells correctly. Inspect the actual stock result. |
| Guarded fanout repair | `v2_fanout_repair.py` substitutes a checkpoint-specific ECO script. It requires `ANTENNA_123` on `net3847`, the expected antenna master, nine signal input loads, and exactly one fanout violation at each corner. | Removes that qualified diode, conditionally repairs routing, then reruns whole-layout DRC, antenna, extraction, electrical checks, and signoff. | The stock action does not run this ECO or name this cell/net. Whether its own route avoids the fanout violation remains unproven. |

The upstream routing script SHA-256 is
`e7e4e4984dff976e96fdf42561b185000bc2a9a3a21553b9bc781cfaef94447f`,
matching the local repair tool's asserted input. The inspection used the
published LibreLane 3.1.0.dev3 wheel. No custom script substitution or checkpoint
ECO is permitted in the stock qualification.

Sources: [pinned TT action](https://github.com/TinyTapeout/tt-gds-action/tree/3412659307918422f3f0727917cf9b499aaca588),
[pinned support tools](https://github.com/TinyTapeout/tt-support-tools/tree/f6bf5c587fba4a4a8abd4c0a03234fccfbf6e61e),
and the local tools linked above. The PDK install fix is present in the pinned
action; it does not by itself prove routing closure for this design.

## Candidate approval and hosted execution

Manual dispatch plus a required environment review was selected because it
binds approval to one workflow run and exact revision without requiring a
release tag or merge into a protected release branch. Tags alone identify a
revision but do not provide the requested per-run approval; branch protection
alone does not identify which candidate was approved for physical execution.

The `v2-physical` environment was created and read back on 2026-10-10:

| Setting | Required and observed value |
| --- | --- |
| Reviewer | `mcranny` (user ID `163694578`) |
| Prevent self-review | `false` |
| Administrator bypass | `false` |
| Deployment policy | Custom branches only |
| Eligible branch names | Exactly `v2-current` and `main`, both of type `branch` |

The workflows read back these settings again before execution and reject
mismatches. The physical job depends on the complete functional gate, then
waits for environment approval. Its six-hour limit does not apply to the
separate functional, precheck, and gate-level jobs.

After the reviewable changes are committed and pushed with authorization,
select `v2-current` or `main` and pass its full workflow revision:

```sh
gh workflow run gds.yaml --ref v2-current -f candidate_sha="$CANDIDATE_SHA"
```

The candidate must equal `github.sha`; the checkout and downstream checks
use that exact SHA. A tag, abbreviated SHA, other branch, dirty source, or
mismatched workflow revision is rejected. Review the functional results and
candidate revision before approving the physical job in GitHub.

The workflow prepares V2 using `v2_candidate.prepare`, saves the original
clean source inventory and snapshot, and records every prepared input hash.
It overlays those prepared files only in the isolated runner checkout. The
manifest explicitly records that transformation; its clean-source identity
refers to the original source, not to an unchanged execution directory.
The existing configuration's standard LibreLane step selection is retained;
no local routing-script substitution or checkpoint ECO is introduced.

The action, support-tools revision, PDK revision, LibreLane version, and
LibreLane container digest are explicit. The container digest is supplied via
the upstream `LIBRELANE_IMAGE_OVERRIDE` option. The stock precheck action and
gate-level action use the produced submission. `test/tt/Makefile` adapts the
latter to `validate_gatelevel.py`, which executes all 14 V2 pin scenarios
against one compiled netlist and checks each shard before merging results.

`tt_qualification.py` independently checks source and artifact hashes,
prepared/resolved configuration, all 583 frozen baseline case identities,
exact suite identities, five induction
successes, six detected mutations, both tops' lint/synthesis records, and the
existing physical acceptance requirements. It preserves raw metrics and adds
only the independently measured unmapped-cell count with its netlist-bound
mapping report. A failed, skipped, missing, empty, or duplicate required result
fails qualification. The next candidate also requires the initialization audit, both RTL reset/X
scenarios, and the V2 netlist reset/X scenario. The already-dispatched `447b3df`
run predates these additions; retain that distinction. Final release
qualification remains a later gate.

Failure artifacts are uploaded with `always()` or failure/cancellation
conditions. Keep the original Actions run and its log even if a hard timeout
prevents final artifact upload. Preserve any completed uploads and download the
run log before starting a fallback. Classify an actual design-rule/electrical/
timing failure as a submission blocker, not as a timeout. Do not substitute a
repaired local layout or modify acceptance thresholds to make this gate pass.

## Six-hour timeout fallback

GitHub-hosted jobs have a [six-hour execution limit](https://docs.github.com/en/actions/reference/limits).
Only a timed-out `gds` job in this exact candidate's dispatched workflow enables
the fallback below. An ordinary cancellation or failed design check does not.
A `cancelled` conclusion with the bound six-hour timeout annotation described
below is a timeout. Keep its
run ID, job conclusions, log, source inventory, and prepared-input hashes.

Use the [official local-hardening procedure](https://tinytapeout.com/guides/local-hardening/)
with the CMOS5L identities above, rather than the guide's generic IHP defaults.
Use a new ordinary clone of the candidate so its `.git` directory is available
inside the retained container. Keep the original source checkout and evidence
intact. Do not use `v2_fresh_route.py`, `v2_antenna_repair.py`, or
`v2_fanout_repair.py` as the fallback.

1. Verify the original run's full SHA, workflow path, and GDS timeout through
   the Actions API, retaining the matching check run and its complete annotations. Create a clean clone at that SHA and
   run the complete functional gate with `--require-clean` into
   `build/qualification/functional` before input preparation.
2. Read the environment and branch policies into
   `build/qualification/physical/environment.json` and `branches.json`.
   Run the same guard and preparation code, passing the original selected
   branch and candidate explicitly:

   ```sh
   python -m tools.tt_qualification guard \
     --directory build/qualification/physical \
     --candidate "$CANDIDATE_SHA" --revision "$CANDIDATE_SHA" \
     --ref refs/heads/v2-current
   python -m tools.tt_qualification prepare --directory build/qualification/physical
   ```

3. Check out the pinned TT action and support-tools revisions in new dependency
   directories. Install the PDK with that action's unchanged
   `install_sg13cmos5l.sh` and a dedicated `PDK_ROOT`; verify the resulting Git
   revision. Place the pinned support-tools checkout at the prepared project's
   `tt` path. Preserve its revision and requirements lock.
4. Use a named, retained container from the exact LibreLane image digest.
   Mount the isolated prepared project at `/work`, the PDK read-only at `/pdk`,
   and run with working directory `/work`. Set `PDK=ihp-sg13cmos5l` and
   `PDK_ROOT=/pdk` inside it. Retain container inspection data, installed Python
   package versions, tool versions, all logs, and the resolved configuration.
   Install the support tools' locked requirements in a retained runtime and
   verify LibreLane is exactly `3.1.0.dev3`. Invoke the stock commands:

   ```sh
   python tt/tt_tool.py --create-user-config --ihp
   python tt/tt_tool.py --harden --ihp --no-docker
   python tt/tt_tool.py --print-warnings --ihp
   python tt/tt_tool.py --create-tt-submission --ihp
   ```

   `--no-docker` prevents a nested disposable container; execution is already
   inside the pinned retained image. This uses the stock Classic flow and
   clean RTL inputs. Retain all `runs/wokwi` checkpoints. Do not resume an
   unverified checkpoint or let `--harden` overwrite a previous run directory.
5. While hardening, sample every 30 seconds: UTC time, `docker stats`,
   container state/OOM status, cgroup `memory.current`, `memory.peak`,
   `memory.events`, and available disk space. Record samples under the physical
   evidence directory. Keep the container after success, failure, interruption,
   or timeout. For example, while the tracked hardening process is active:

   ```sh
   while kill -0 "$HARDEN_PID" 2>/dev/null; do
     date -u
     docker stats --no-stream --format '{{json .}}' "$CONTAINER_NAME"
     docker inspect --format '{{json .State}}' "$CONTAINER_NAME"
     docker exec "$CONTAINER_NAME" sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak /sys/fs/cgroup/memory.events'
     df -k "$RUN_ROOT"
     sleep 30
   done > "$RESOURCE_LOG" 2>&1
   ```

6. Run the pinned TT precheck's commands in a retained compatible runtime,
   using its requirements and Nix dependencies, the same PDK, and the actual
   generated GDS. Preserve all reports under `tt/precheck/reports`. Run the
   full V2 pin suite against the actual `tt_submission` netlist using
   `validate_gatelevel.py`, with output `test/tt/output`. Run the independent
   mapping audit against that netlist in the pinned LibreLane image. All
   required timing/electrical metrics and prechecks must pass unchanged.
7. Retain the original clean-source `origin.json` with each stage directory.
   Record each result with `tt_qualification` using `--execution-path local_tt_flow`:
   `physical`, `precheck`, and `gatelevel`. The physical mapping report belongs
   at `build/qualification/physical/mapping.json`. These operations verify the
   real submission, PDK/support checkout identities, and result hashes; they
   do not rerun the tools. Run the common checker:

   ```sh
   python -m tools.tt_qualification accept --directory build/qualification \
     --candidate "$CANDIDATE_SHA" --execution-path local_tt_flow
   ```

8. Archive the complete source/preparation records, functional and physical
   evidence, TT submission, precheck and gate-level reports, resolved config,
   container/runtime identities, monitor output, original timeout evidence,
   and passing qualification summary. Record the archive's SHA-256 and durable
   artifact location. This archive is a stock-flow qualification bundle; the
   final release additionally needs reset/X and full-geometry evidence.

The hosted `447b3df` job reached the six-hour timeout. Its clean local fallback
subsequently failed on one reported routing short; the verified checkpoint
recovery below preserves that failure and records its own results separately.

## Validated checkpoint recovery

Source `447b3dfd0986d473c71afa28efafb3a5cff48da7` completed a fresh-process
recovery on 14 October 2026. The original local Classic flow failed after
40 hours 31 minutes with one Metal4 short. The saved geometry showed a 0.280 µm
gap at the marker, and a fresh full-design check with the same OpenROAD binary
reported no DRC. These observations support stale in-process router state;
they do not establish the exact upstream cause.

Recovery loaded the hashed, unchanged post-antenna checkpoint and reran stock
`OpenROAD.DetailedRouting` and every downstream check. No custom routing script,
diode power reconnection or checkpoint fanout ECO was used. The original failure
and its artifacts remain intact. The resolved configuration changed only:
explicit eight-thread routing, retained DRC snapshots/reports, and enabling
KLayout DRC with eight threads. Fixed physical constraints were unchanged.

The recovered flow finished in 50 minutes 40 seconds. Routing, antenna, Magic
DRC, KLayout DRC, LVS, timing and electrical violation counts were zero. Fresh
fast/typical/slow timing reports have minimum setup slack 18.456291 ns and
minimum hold slack 0.054473 ns. Independent checks subsequently passed:

- Nine required stock TT prechecks.
- All 14 V2 gate-level pin scenarios from the physical source revision.
- Mapping audit: 70,022 cells, zero unmapped cells.
- Full-geometry inventory: 5,813,030 expanded polygons, no omitted layers.
- Four-state startup/reset/state-loss audit from `c1c9be5`, explicitly bound to
  the recovered netlist rather than relabeled as original `447b3df` test code.
- The unchanged physical checker with exact required suite identities.

The GDS SHA-256 is
`902c78fce4a69fde56dbf209b34d5c809e2d44610e6b1e790f03963bb97322c6`;
the netlist SHA-256 is
`d99cc8992fe8fc71a5a7dc5c0fd40f162a5f84a1e2fc9cf7a1166c069bda1a84`.
Gate-level simulation uses functional cell models without SDF; timing is
established separately by extracted all-corner STA.

This establishes passing recovered-layout checks without the historical custom
repairs. It does not establish a successful uninterrupted stock invocation from
RTL, final revision acceptance, or competition acceptance. The local manifest
retains `execution_path: local_tt_flow_checkpoint_rerun`. A reporting envelope
may use `local_tt_flow` only with the mandatory nested recovery record below.

## Record local evidence in CI

`tt-local-report.yaml` is a reusable workflow called by the existing `gds.yaml`
entrypoint in `record_local` mode. This is a separate reporting run, with a
distinct run name and no functional or physical execution jobs. It takes a
full tested candidate SHA and a compact JSON report. The approved reporter runs
from the selected workflow revision, records that revision separately, and
requires the tested candidate to be its ancestor. It checks out full Git history
and verifies the reporter checkout and approval settings before reading the
report. Qualification mode still requires candidate SHA equal to workflow SHA.
It requires `v2-physical` approval and verifies the original run and timed-out
physical job through GitHub's API. GitHub can report a six-hour timeout as
`cancelled`; that conclusion is accepted only with the explicit six-hour timeout
failure annotation on the matching GitHub Actions check run. Ordinary cancellations
and design failures remain rejected. CI retains the original conclusion, check-run
identity, and complete annotations rather than relabeling the hosted result. The report has schema `1`, execution path
`local_tt_flow`, the exact source and dependency identities, `timed_out_run_id`,
`bundle_location`, and three SHA-256 fields: `bundle_sha256`,
`source_inventory_sha256`, and `prepared_inputs_sha256`. Hash each inventory's
canonical JSON (sorted keys, compact separators, UTF-8) and retain that exact
JSON file in the archive.

`result_summary` records `passed: true`, the actual `pytest` count (at least
583), `v1_core: 3`, `v1_pins: 3`, `v2_core: 3`, `v2_pins: 14`, `capture: 1`,
`induction_proofs: 5`, `detected_mutations: 6`, `precheck: 9`, `gatelevel: 14`,
and `required_violations: 0`. From the reset-audit revision onward also require
`rtl_reset_suites: 2`, `gatelevel_reset_suites: 1`, and
`initialization_elaborations: 7`. Do not report these for the earlier `447b3df`
run unless the additional checks were actually executed against its artifacts.

This reporting run records locally validated evidence. It checks report
fields and original timeout identity; it does not independently rerun physical
checks or download and validate the full archive. Its artifact explicitly sets
`ci_reran_physical_checks: false`. The original timed-out run remains visible.

A successful stock hosted run instead records `execution_path: github_actions`
in `qualification.json`. Both paths remain subject to the same physical
requirements. Re-query the CMOS5L action branch immediately before the final
candidate freeze; review any update and rerun functional and physical gates.

Schema `2` records this reviewed checkpoint recovery without claiming final
release acceptance. It requires `release_acceptance: false`, the original
physical-source functional counts, and a separate supplemental reset-audit
source/hash/netlist identity. Its `recovery` object records `kind:
checkpoint_rerun`, source, failed parent result and failure, checkpoint and
manifest hashes, exact configuration changes, unchanged stock scripts, no ECOs,
validation-summary hash, and GDS/netlist hashes. Missing evidence, altered
configuration, failed checks, or mixed-source counts are rejected. Schema `1`
retains the existing complete same-source result requirements.

Dispatch the report through the existing workflow, which is already registered
on the default branch, using the reviewed reporter revision on an eligible branch:

```sh
gh workflow run gds.yaml --ref v2-current \
  -f candidate_sha="$CANDIDATE_SHA" -f mode=record_local \
  -F report_json=@local-summary.json
```

This avoids requiring a preliminary merge solely to register a new manual
workflow. [GitHub requires a manual workflow to exist on the default branch](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow);
the existing entrypoint calls the reusable report workflow from the selected
revision. Unknown modes fail before any source checkout or qualification job.

## Reset-audit addition

Before sealing the gate-level stage for candidates containing the Step 3 audit,
run the following from the original clean candidate checkout (the hosted adapter
runs it automatically):

```sh
python -m tools.validate_reset --version 2 \
  --netlist tt_submission/tt_um_mcranny_protocol_emulator_v2.v \
  --pdk-root "$PDK_ROOT" --output test/tt/output/reset
```

The seal binds that audit and its exact XML identity to the same netlist and
source as the 14 ordinary pin scenarios. The full functional runner separately
runs both RTL reset scenarios and all seven initialization elaborations.
