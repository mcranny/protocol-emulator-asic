.PHONY: test python-test rtl-test lint formal synth v1-test v1-rtl-test v1-lint v1-formal v1-synth v2-test v2-formal v2-capture-test

test: python-test lint rtl-test formal synth

python-test:
	python -m pytest -q --junitxml=build/python-results.xml

rtl-test:
	$(MAKE) -C test -f Makefile.v2
	$(MAKE) -C test
	$(MAKE) -C test -f Makefile.v2capture

lint:
	verilator --lint-only --Wno-DECLFILENAME --top-module tt_um_mcranny_protocol_emulator_v2 src/*.v

formal: v2-formal

synth:
	mkdir -p build
	yosys -Q -T -q -p 'read_verilog src/*.v; synth -top tt_um_mcranny_protocol_emulator_v2; stat' -l build/synthesis.log

v1-test: python-test v1-lint v1-rtl-test v1-formal v1-synth

v1-rtl-test:
	$(MAKE) -C test -f Makefile.core
	$(MAKE) -C test -f Makefile.v1pins

v1-lint:
	verilator --lint-only --Wno-DECLFILENAME --top-module tt_um_mcranny_protocol_emulator src/*.v

v1-formal:
	mkdir -p build
	yosys -Q -T -q -s formal/engine.ys -l build/formal-engine.log
	@grep 'Induction step proven: SUCCESS' build/formal-engine.log

v1-synth:
	mkdir -p build
	yosys -Q -T -q -p 'read_verilog src/*.v; synth -top tt_um_mcranny_protocol_emulator; stat' -l build/synthesis-v1.log

v2-test: test

v2-formal:
	mkdir -p build
	yosys -Q -T -q -s formal/v2_engine.ys -l build/formal-v2-engine.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-engine.log
	yosys -Q -T -q -s formal/v2_integration.ys -l build/formal-v2-integration.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-integration.log
	yosys -Q -T -q -s formal/v2_input_filter.ys -l build/formal-v2-input-filter.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-input-filter.log
	yosys -Q -T -q -s formal/v2_capture.ys -l build/formal-v2-capture.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-capture.log

v2-capture-test:
	verilator --lint-only --top-module protocol_capture_v2 src/v2_capture.v
	$(MAKE) -C test -f Makefile.v2capture
	mkdir -p build
	yosys -Q -T -q -s formal/v2_capture.ys -l build/formal-v2-capture.log
	@grep 'Induction step proven: SUCCESS' build/formal-v2-capture.log
