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
models under PDK_ROOT, including sg13cmos5l_udp.v: the standard-cell file
instantiates primitives defined there. Routed timing is a separate
static-analysis gate; this test does not enable SDF delay annotation.

To reproduce against an already downloaded submission without copying its
netlist into the source tree:

```sh
make -C test GATES=yes PDK_ROOT=/path/to/pdk \
  GATE_LEVEL_NETLIST=/path/to/tt_submission/tt_um_mcranny_protocol_emulator.v \
  COCOTB_RESULTS_FILE=results_gl.xml
```

Use the PDK revision recorded in the same submission's pdk.json. Missing
library files or unresolved primitives must fail elaboration; never replace
cells with stubs or substitute RTL for the submitted netlist.

Results are in results.xml/results_core.xml. Pin-level waveforms are in tb.fst.
Reproduction data and divergence reports are retained under output/.
