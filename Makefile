.PHONY: test python-test rtl-test lint formal synth v2-test v2-formal v2-capture-test

test: python-test lint rtl-test formal

python-test:
	python -m pytest -q --junitxml=build/python-results.xml

rtl-test:
	$(MAKE) -C test -f Makefile.core
	$(MAKE) -C test

lint:
	verilator --lint-only --Wno-DECLFILENAME --top-module tt_um_mcranny_protocol_emulator src/*.v

formal:
	mkdir -p build
	yosys -Q -T -q -s formal/engine.ys -l build/formal-engine.log
	@grep 'Induction step proven: SUCCESS' build/formal-engine.log

synth:
	mkdir -p build
	yosys -Q -T -q -p 'read_verilog src/*.v; synth -top tt_um_mcranny_protocol_emulator; stat' -l build/synthesis.log

# Candidate checks are explicit until V2 replaces the accepted top-level design.
v2-test: python-test
	verilator --lint-only --top-module protocol_engine_v2 src/v2_engine.v
	verilator --lint-only --top-module tt_um_mcranny_protocol_emulator_v2 src/*.v
	$(MAKE) -C test -f Makefile.v2
	$(MAKE) -C test -f Makefile.v2pins
	$(MAKE) v2-formal

v2-formal:
	mkdir -p build
	yosys -Q -T -q -s formal/v2_engine.ys -l build/formal-v2-engine.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-engine.log

v2-capture-test:
	verilator --lint-only --top-module protocol_capture_v2 src/v2_capture.v
	$(MAKE) -C test -f Makefile.v2capture
	yosys -Q -T -q -s formal/v2_capture.ys -l build/formal-v2-capture.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-capture.log
