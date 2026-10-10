# Bounded firmware playback

`protocol_emulator.v2.replay.compile_schedule` converts a finite output schedule
into ordinary `DRIVE`, `DELAY` and `HALT` instructions. It uses the existing
timing engine and requires no replay hardware. The
[UART scenario runner](v2-scenarios.md) uses this component alongside
external-stimulus regression replay.

```python
from protocol_emulator.v2.replay import OutputEvent, compile_schedule

words = compile_schedule(
    [OutputEvent(0, 0, 1), OutputEvent(25, 1, 1), OutputEvent(50, 0, 1)],
    end_cycle=74, owner=1,
)
device.load(0, words)
```

Cycle zero is the first instruction execution edge after START. Each event
specifies output values and enables immediately after its edge. The first
event must explicitly specify cycle zero. The final state remains driven
through `end_cycle`; HALT releases the outputs on `end_cycle + 1`. Configure
the same ownership and open-drain masks on the stopped engine before loading
and starting it.

All timestamps must be strictly increasing integer system cycles, within a
32-bit bounded window. One atomic value/enable change per cycle is supported.
Fractional spacing, duplicate timestamps, unowned enables, nonzero values for
released pins and actively high open-drain outputs are rejected. Long waits
are split across 16-bit DELAY instructions without adding waveform cycles.
Capacity checking includes every DRIVE, DELAY and the final HALT; an oversized
schedule is rejected before generating any firmware. The selected hardware
candidate has 64 words; the 128-word option is for feasibility experiments.

Playback reproduces output values and enables relative to the first scheduled
execution edge. Capture samples **before** execution, so a recorded output
change appears one sample after the instruction that produced it. Neither
these instructions nor synchronized captured inputs recover the asynchronous
phase of an external peer. Markers, running flags and input observations are
not output schedules.

Python tests compare every output cycle against independent randomized
schedules, check capacity boundaries and split long delays. The pin-driven
test loads the generated words over host SPI and checks every execution edge,
including adjacent events, open-drain release and final HALT. Routed-netlist
validation remains required before this capability is accepted.
