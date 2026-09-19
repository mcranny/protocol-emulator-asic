.PHONY: test python-test rtl-test lint formal synth

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
