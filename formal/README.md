# Induction proofs

Every proof uses Yosys SAT temporal induction with `-set-assumes`,
`-prove-asserts`, and `-verify`. Success requires the log entry
`Induction step proven: SUCCESS!`; a bounded pass is insufficient. The five
scripts retain their existing command sequence, initial depth, maximum depth,
assertions, and assumptions. Their standardized headers identify the proof,
top, assumptions, properties, method, and this scope document.

| Proof ID | Script and top | Initial depth | Properties |
| --- | --- | --- | --- |
| F-V1-ENGINE | `engine.ys`: `protocol_engine` | 4 | FIFO level <=4, structural PC bound, released outputs, busy-write data/validity preservation, fault/illegal-fetch halt |
| F-V2-ENGINE | `v2_engine.ys`: `protocol_engine_v2` | 4 | TX/RX levels <=16, pointer distance and tracked-byte order, stack depth <=2, ownership masking, open-drain safety, released stopped/disabled outputs |
| F-V2-CAPTURE | `v2_capture.ys`: `protocol_capture_v2` | 4 | Capture/timestamp bounds, armed/truncated consistency, zero invalid-address reads, stopped-state stability |
| F-V2-FILTER | `v2_input_filter.ys`: `protocol_input_filter_v2` | 5 | Three agreeing samples qualify high/low; disagreement does not change the filtered bit |
| F-V2-INTEGRATION | `v2_integration.ys`: `tt_um_mcranny_protocol_emulator_v2` | 4 | Disjoint ownership, open-drain output gating, safe reset/disable enables, atomic start/rejection, FIFO admission, stopped-only writes and configuration stability |

All scripts allow at most 16 induction steps. `async2sync` and `dffunmap`
normalize sequential cells for the solver; these proofs do not establish
asynchronous analog behavior, metastability, or reset recovery/removal timing.
Use the full functional runner for retained logs and all five proofs:

```sh
python tools/validate_local.py --output build/validation-new
```

Individual entrypoints remain `make formal`, `make v2-formal`, and
`make v2-capture-test`. The full gate also requires the six mutation checks,
including `ownership_overlap`; failure to detect that mutation fails the gate.

## Reset and unconstrained state

Each proof assumes active reset on the initial sampled clock edge. Later input
values, enable transitions, commands, pins, events, and resets are arbitrary
except for the integration assumptions listed below. The initialization of
`past_valid`, `formal_past_valid`, or the filter's three-bit `valid` shift
register controls proof-history availability. These registers exist only under
formal preprocessor guards and do not initialize the DUT's synthesized state.

The filter properties additionally require the relevant current and three
previous reset samples to be released. Queue ghost registers are reset with
the corresponding validity tracking; their expected data are consulted only
while a selected transaction is tracked. Ghost transaction selection is
arbitrary, not an assumption limiting input traffic.

Uninitialized program, FIFO, and capture memory are unconstrained. Reset clears
validity/count state rather than the memory arrays. The initialization audit
elaborates both ordinary tops and all formal modules before optimization,
rejects any initialized synthesized state, and permits only named proof
bookkeeping initialization in formal mode. It also rejects initialized memory.
See [reset and power behavior](../docs/reset-and-power.md) for the four-state
simulation checks, which are separate from the solver's logical model.

## Memory and execution abstractions

The V1 proof selects an arbitrary constant program address to check busy-write
preservation. Its six-bit PC bound is structural; instruction boundary and
last-word fallthrough behavior also need simulation tests.

The V2 engine replaces each fetched instruction with an independent arbitrary
24-bit value under `FORMAL`. This overapproximates instruction sequences for
safety; it does not prove the program store's fetch contents match host writes.
Readback and differential simulation cover that relationship. TX/RX data order
uses arbitrary transaction selection and distance counters independent of the
implementation's circular pointers. Full/empty pointer equality is meaningful
only together with the separately bounded level.

Capture uses the default 32 records and 32-bit timestamp. These runs do not
claim arbitrary parameter values, complete waveform reconstruction, or
interference freedom at the top. Those behaviors have separate differential
and pin-level tests.

## Integration assumptions and limitations

The integration script black-boxes `protocol_host_spi`, `protocol_engine_v2`,
`protocol_capture_v2`, and `protocol_input_filter_v2`, then uses
`-ignore_unknown_cells`. Their outputs are abstract signals. The top states
these assumptions explicitly in `FORMAL_INTEGRATION`:

| Assumption | Exact restriction | Relationship to implementation |
| --- | --- | --- |
| Engine ownership | `oe0 & ~owner0 == 0`, likewise engine 1 | Engine safety assertion; no engine drives an unowned bit |
| Open-drain behavior | `oe0 & out0 & od0 == 0`, likewise engine 1 | Engine safety assertion; owned open-drain bits never actively drive high |
| Host reset | While internal `reset_n` is low, `cmd_valid` and `frame_error` are low | Host reset implementation and simulation; there is no separate host induction proof |
| FIFO bounds | `txlevel0 <= 16` and `txlevel1 <= 16` | Engine level assertions |
| Readiness | `readyN == (tx_count != 0 && txlevelN + tx_count <= 16)` with the exact widened arithmetic in RTL | Engine combinational admission expression; the integration proof assumes this equality |

Other black-box outputs remain arbitrary. In particular, program validity,
start readiness, RX data/levels, capture contents, and filtered samples do not
receive invented correctness assumptions. Atomic-start checks establish the
top's behavior relative to the abstract readiness values; they do not prove
that those values imply a complete program or protocol success.

This is compositional safety evidence with an explicit assumption boundary,
not a monolithic proof of host decoding, capture contents, or protocol timing.
Reviewing and strengthening the relationships between components belongs to
[Track 2](../docs/verification-track-2.md), after the candidate baseline freezes.
