# V2 host API

[The architecture](v2-architecture.md#host-transport) specifies every command,
response field and wire interval. `protocol_emulator.v2.host.Device` wraps a
five-byte `exchange` transport; `connect` probes the version before choosing
V1 or V2. The model's `Device` is a separate cycle-level specification, not a
transport adapter. Hardware transport operation has not been demonstrated.

| Python operation | Behavior |
| --- | --- |
| `check_version()`, `capabilities()` | Require version 0x020240; read two engines, 64 words, 16 TX/RX entries, 32 capture records and features |
| `load(engine, words)` | Validate complete V2 image and stopped engine; fill unused words with HALT and verify all 64 |
| `configure(engine, owner, open_drain=0)` | Require disjoint ownership and open-drain subset, with both engines stopped |
| `configure_filter(mask)` | Check feature support, set and read back the global filter mask |
| `start(mask=3)` | Atomic start of selected engines; all must be ready |
| `stop(engine)`, `reset(engine)`, `status(engine)` | Stop, state reset, or inspect one engine |
| `fifo_write(engine, data)` | Up to 16 bytes, packed three per frame; return accepted count |
| `fifo_read(engine, count=16)` | Up to 16 `(byte, metadata)` records; empty replies omitted |
| `request(command, engine=0, address=0, value=0)` | One command with response flush |
| `batch(requests, allow_empty=False)` | Up to 64 prevalidated frames, pipelined, then one final NOP |

Watch commands 0x22/0x23 pack mask in bits 7:0, value in 15:8 and edge mask in
23:16. Command 0x24 sets the two-bit wait-abort mask. Apply these to a stopped
engine, following the [example configurations](example-programs.md).
[Capture](v2-capture.md) and [scenario preparation/readout](v2-scenarios.md)
document their higher-level APIs.

A response arrives during the frame following its request. RX consumption commits
only after that response is fully delivered. A malformed/short frame leaves the
reserved record queued and halts execution. `BatchError` preserves completed
responses and the submitted/attempted count; transport failure may leave the
last delivery unknown. Do not blindly retry failed batches. Stop, inspect status,
and explicitly recover using the [reset sequence](reset-and-power.md) if needed.
