.PHONY: all clean
all:
	python ../../tools/validate_gatelevel.py --netlist $(CURDIR)/gate_level_netlist.v --pdk-root $(PDK_ROOT) --output $(CURDIR)/output --jobs 3
	cp output/gatelevel.xml results.xml

# The TT action calls clean on a fresh job. Reject stale results rather than
# removing retained evidence from an earlier attempt.
clean:
	test ! -e output
	test ! -e results.xml
