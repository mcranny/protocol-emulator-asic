"""Check a V3 routed netlist against the RTL with eqy in the pinned image.

The RTL is read with the same define synthesis used, so each SRAM is the same
black box on both sides. The netlist's cells take their function from the
CMOS5L liberty file. Only ports and registers are paired, by name, which the
netlist keeps on each flop's output net, and every partition is proved with
Yosys's own SAT solver; the image ships no external SMT solver.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

import yaml

from tools.local_flow import IMAGE

TOP = "tt_um_mcranny_protocol_emulator_v3"
LIBERTY = "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib"
DEFINE = "V3_SRAM_BLACKBOX"
DEPTH = 5
#: RTL nets on the SRAM pins. The black box's pins are already paired, and the
#: netlist reaches them through buffers, so pairing these names as well would
#: pair one RTL bit with two netlist bits.
SRAM_NETS = ("u_progmem*.a_*", "u_progmem*.write_data*")
#: RTL registers synthesis merged into an identical register, each paired with
#: the one it kept. The input filter's first two samples are the capture
#: synchronizer's two stages.
MERGED = (
    (r"/u_filter\.previous\[(\d+)\]/", r"/cap_sync0\[\1\]/"),
    (r"/u_filter\.older\[(\d+)\]/", r"/cap_sync1\[\1\]/"),
)
#: Flop cells on each side, whose ``Q`` nets keep their names.
GOLD_FLOPS = "t:$*dff*"
GATE_FLOPS = "t:sg13cmos5l_df*"


def hide(flops: str) -> str:
    """A selection of every wire that is not a port or a flop's ``Q`` or ``CLK``.

    Hiding these names leaves eqy only ports, registers and clocks to pair. A
    named combinational net is a poor cut point: synthesis may compute part of
    its fanout from other nets, and the proof then treats it as a free input.
    Clock-tree nets, which clock tree synthesis names ``clknet_*``, keep their
    names: eqy traces each paired register's clock back to the clock port
    through them. The RTL is flattened with no clean-up after, so the clock
    alias on each SRAM's clock pin stays a wire of its own, as the netlist's
    buffered SRAM clock is.
    """
    return (f"w:* {flops} %co1:+[Q] w:* %i %d "
            f"{flops} %ci1:+[CLK] w:* %i %d w:clknet_* %d x:* %d")


def sources(info: Path) -> list[str]:
    """The RTL files ``info.yaml`` names, in its order."""
    project = yaml.safe_load(info.read_text())["project"]
    if project["top_module"] != TOP:
        raise ValueError(f"{info}: top module is {project['top_module']}, not {TOP}")
    files = project["source_files"]
    if not files or any(Path(f).name != f or not f.endswith(".v") for f in files):
        raise ValueError(f"{info}: expected plain Verilog file names")
    return files


def config(files: list[str], src: str, netlist: str, liberty: str) -> str:
    """The eqy configuration, with paths as the container sees them."""
    rtl = " ".join(f"{src}/{name}" for name in files)
    nomatch = "\n".join([*(f"gold-nomatch {pattern}" for pattern in SRAM_NETS),
                         *(f"gold-match {gold} {gate}" for gold, gate in MERGED)])
    return f"""[options]
splitnets on

[gold]
read_verilog -D{DEFINE} -I{src} {rtl}
prep -top {TOP}
memory_map
opt_clean
flatten
rename -hide {hide(GOLD_FLOPS)}

[gate]
read_liberty -ignore_miss_func {liberty}
read_verilog -D{DEFINE} {src}/v3_sram_blackbox.vh
read_verilog {netlist}
prep -top {TOP}
opt_clean
rename -hide {hide(GATE_FLOPS)}

[match *]
{nomatch}

[strategy sat]
use sat
depth {DEPTH}
"""


def script(jobs: int) -> str:
    """The container command: partition with eqy, then prove every partition.

    eqy runs its proofs through ``make``, which the image lacks, so the
    partitions are set up with ``-m`` and each one's ``run.sh`` runs directly.
    """
    return ("eqy -m -d /out/run /out/v3.eqy && "
            f"ls -d /out/run/strategies/*/* | xargs -P {jobs} -I{{}} "
            "bash -c 'cd {} && source run.sh'")


def summarize(strategies: Path) -> dict:
    """Each partition's proof status, and PASS only if every one passed."""
    partitions = {}
    for run in sorted(strategies.glob("*/*/run.sh")):
        status = run.parent / "status"
        partitions[run.parent.parent.name] = (
            status.read_text().strip() if status.is_file() else "NOT RUN")
    failed = sorted(name for name, status in partitions.items() if status != "PASS")
    return {"status": "PASS" if partitions and not failed else "FAIL",
            "partition_count": len(partitions), "failed": failed,
            "partitions": partitions}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--netlist", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args(argv)
    source, netlist, pdk, output = (p.resolve() for p in
                                    (args.source, args.netlist, args.pdk_root, args.output))
    files = sources(source / "info.yaml")
    if not netlist.is_file():
        parser.error(f"{netlist}: no such netlist")
    output.mkdir(parents=True, exist_ok=False)
    (output / "v3.eqy").write_text(
        config(files, "/src", f"/netlist/{netlist.name}", f"/pdk/{LIBERTY}"))
    command = ["docker", "run", "--rm", "--network", "none",
               "-v", f"{source / 'src'}:/src:ro", "-v", f"{netlist.parent}:/netlist:ro",
               "-v", f"{pdk}:/pdk:ro", "-v", f"{output}:/out", "-w", "/out", IMAGE,
               "bash", "-c", script(args.jobs)]
    with (output / "eqy.log").open("w") as log:
        result = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
    summary = summarize(output / "run" / "strategies")
    summary.update(netlist=str(netlist), image=IMAGE, exit_code=result)
    (output / "equiv.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k != "partitions"}, indent=2))
    return 0 if result == 0 and summary["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
