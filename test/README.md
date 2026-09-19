# Simulation

Install the pinned Python dependencies in requirements.txt and Icarus Verilog.
From the repository root:

```sh
make -C test -f Makefile.core
make -C test
```

The core suite compares all architectural state against the Python model on
each edge. The top-level suite uses only external pins and independently
checks SPI framing/readback, error containment, UART bit cells, simultaneous
host traffic, phase offsets, and reset/disable/stop during transmission.

The GDS workflow copies the submitted netlist into gate_level_netlist.v and
runs `GATES=yes make`. This functional netlist test uses the CMOS5L library
models under PDK_ROOT. Routed timing is a separate static-analysis gate.

Results are in results.xml/results_core.xml. Pin-level waveforms are in tb.fst.
Reproduction data and divergence reports are retained under output/.
