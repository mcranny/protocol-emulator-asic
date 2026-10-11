# Assembler and toolchain

## Install and run

From the source checkout, use Python 3.11 or later in a virtual environment:

```sh
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
python -m pip install -r test/requirements.txt
python examples/v2/run.py --output build/examples-new
```

The last command generates 16 assembly programs and images and runs 14 checked
protocol scenarios, including TX/RX together at each UART rate. Output directories
must be new. The [example matrix](example-programs.md) specifies pins and expected
traces. These are cycle-model executions; the independent RTL pin suites establish
top-level SPI-host and protocol behavior separately.

## Assembly, disassembly and image format

V2 uses the existing Python APIs. The installed `protocol-emulator` CLI selects
V1 behavior and does not provide a V2 assembler/run mode. The V1 image helpers
also validate V1 encodings; use V2 `decode` for extension instructions.

```python
from pathlib import Path
from protocol_emulator.v2.firmware import uart_tx
from protocol_emulator.v2.isa import assemble, decode

source = uart_tx(period=217, pin=0)
words = assemble(source, capacity=64)
image = b"".join(word.to_bytes(3, "big") for word in words)
Path("uart-v2.bin").write_bytes(image)
# Decode each instruction; there is no dedicated V2 disassemble function.
text = "\n".join(
    i.name + (" " + ", ".join(map(str, i.operands)) if i.operands else "")
    for i in map(decode, words)
)
assert assemble(text, capacity=64) == words
```

Images contain 1–64 words, three bytes per word, most-significant byte first,
with no header. They contain at most 192 bytes. Labels identify instruction
addresses, not byte offsets; decimal and `0x`/`0b` integer literals are accepted.
Comments start with `#` or `;`. Decode every word and reject incomplete words or
out-of-range targets before sending an image. Explicit `capacity=64` matters:
the feasibility assembler and model otherwise default to 128 words.

## Model execution

```python
from protocol_emulator.v2.model import Engine
engine = Engine(capacity=64)
engine.owner = 1
engine.load(words)
engine.push([0x55, 0xA5])
engine.start()
for cycle in range(22 * 217):
    engine.tick(0)
    value, enables = engine.pins
```

Model inputs are already synchronized. A board-level stimulus needs an explicit
synchronizer/pin simulator. Model host operations are atomic Python calls;
this loop does not model 40-clock SPI frames or operating-system scheduling.
See runnable examples for independent decoding and input pipelines.

## Loading and host control

A transport supplies `exchange(frame: bytes) -> bytes` for exactly one five-byte
mode-0 SPI transaction. Given an initialized compatible transport:

```python
from protocol_emulator.v2.host import Device
host = Device(transport)
host.check_version()
host.stop(0)
host.stop(1)
host.load(0, words)        # validate, HALT-pad, write and read back all 64 words
host.configure(0, owner=1)
host.fifo_write(0, bytes([0x55, 0xA5]))
host.start(mask=1)
```

Initialize/reset the physical design first using [the documented sequence](reset-and-power.md).
The snippet assumes the other engine owns no conflicting pins. `connect(transport)`
can choose the V1/V2 host backend by version probe; selection of a V2 ASIC default
does not change the CLI. Watch and abort commands, packed FIFO service, capture,
and failure handling are described in [the host API](host-api.md).
