# Induction proofs

Every proof uses Yosys SAT temporal induction with `-set-assumes`,
`-prove-asserts`, and `-verify`. Success requires the log entry
`Induction step proven: SUCCESS!`; a bounded pass is insufficient. The V1 and
V2 proof scripts are on the `v2` tag. The scripts in this directory are the V3
proofs. Each header names the proof, the top, the assumptions and the
properties. All of them allow at most 16 induction steps.

`async2sync` and `dffunmap` normalize sequential cells for the solver. These
proofs do not establish asynchronous analog behavior, metastability, or reset
recovery timing. Uninitialized program and capture memory is unconstrained:
reset clears the validity and count state, not the memory arrays.

Run one from the repository root in WSL, for example `yosys -q formal/v3_crc.ys`.

## V3 program memory

`v3_progmem.ys` (F-V3-PROGMEM, top `v3_progmem`, initial depth 4) proves, on the
inferred register-file model: the macro tie-offs (`A_DLY` high, every `A_BIST_*`
input low, all byte-mask bits set), `A_MEN` equal to `fetch_en | write_en` and
low when neither is asserted, no read and write overlap on the single port, data
at N+1 equal to the word last written to an arbitrary constant address,
`image_complete` equal to all 256 bitmap bits set, a write setting exactly one
bitmap bit, and `invalidate` clearing all of them. The proof does not cover the
hard macro; the same cocotb tests run against the PDK behavioral model with
`DEFINES=-DFUNCTIONAL` and check its one-cycle registered read.

## V3 host SPI

`v3_host_spi.ys` proves `v3_host_spi` (proof ID F-V3-HOST-SPI) by temporal
induction, then checks reachability with bounded runs. Run it from the
repository root in WSL with `yosys -q formal/v3_host_spi.ys`.

| Property | Statement |
| --- | --- |
| Synchronization | `sck`, `cs_n` and `mosi` each reach their second flop exactly two clocks after they are sampled, and the edge-detect registers one clock later |
| Exact-48 acceptance | `cmd_valid` is high exactly one clock after a synchronized deselect that followed 48 synchronized SCK rising edges |
| Rejection | Any other count, including 47 and 49, raises `frame_error` instead; the two never coincide |
| MISO gating | `miso` is low whenever the raw `cs_n` is high, and equals the shift register's top bit while it is low |
| Response timing | The reply captured with `cmd_valid`, or the reject word after `frame_error`, is held through the deselect and is what the following frame shifts out; while selected only zeros shift in behind it, so a frame's own bits never reach MISO |

The proof assumes an initial reset and that `cs_n` stays high for at least four
clocks after it rises (the response must be loaded before MISO is enabled
again); the host must honour this spacing. The count is checked against a ghost
counter over the synchronized signals, and the synchronizer chain against the
raw inputs, so SCK edges that land on the synchronized select or deselect are
outside what the proof says about the pins. Three bounded runs show that frames
of 48, 47 and 49 edges are reachable under the same assumptions. Frame data
bits and the reply value are covered by simulation (`test/Makefile.v3host`),
not by the proof.
