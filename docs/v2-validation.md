# V2 behavioral validation

Run `make test`, `make v2-test`, `make v2-capture-test` and
`python tools/v2_mutations.py` with the dependencies in `test/requirements.txt`,
Icarus Verilog, Verilator and Yosys. V1 and V2 CI jobs are independent.

Core and capture differential tests compare state against the software models.
Pin suites use independent protocol peers, bounded replay and fault/recovery
scenarios. Formal induction checks engine, capture, integration and filter
safety under the assumptions described in the
[architecture and host interface](v2-architecture.md). Mutation checks require
actual detection of the selected defects, not compilation failures.

These checks establish their exercised behavior and stated proof properties.
They do not establish physical closure, complete firmware protocol correctness,
or operation on a physical board. PR validation records identify the source
revision and actual commands and suite counts for each run.
