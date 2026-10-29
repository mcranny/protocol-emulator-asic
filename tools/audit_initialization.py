"""Reject initialized synthesized state and initialized memory in Yosys elaboration."""

import argparse
import json
import subprocess
from pathlib import Path

from tools.check_physical import checksum

ROOT = Path(__file__).resolve().parents[1]
TOPS = ("tt_um_mcranny_protocol_emulator_v3",)
# Proof bookkeeping registers. Each is initialised to 0 and exists only under
# FORMAL, so a synthesized netlist must not contain any of them.
BOOKKEEPING = {
    "protocol_engine": {"past_valid"},
    "protocol_engine_v2": {"past_valid"},
    "protocol_capture_v2": {"past_valid"},
    "protocol_input_filter_v2": {"valid"},
    "tt_um_mcranny_protocol_emulator_v2": {"formal_past_valid"},
    "v3_capture": {"past_valid"},
    "v3_engine": {"f_past_valid"},
    "v3_pin_cell": {"valid"},
    "v3_input_filter": {"valid"},
    "v3_host_spi": {
        "started", "hist", "hi_run",
        "g_count", "g_count_q", "g_fall", "g_end_q", "g_resp",
    },
    "v3_fifo": {"started"},
    "v3_timebase": {"started"},
    "v3_crc": {"started"},
    "v3_progmem": {"past_valid", "watched_written", "hit_r"},
}
# Formal-only registers whose reset value is not zero. cs_q is the registered
# previous CS_n, which idles high, so the proof starts it at 1.
BOOKKEEPING_VALUE = {("v3_host_spi", "cs_q"): "1"}


def module_base(module_name):
    """Return the source module name, dropping a Yosys ``$paramod`` prefix."""
    if module_name in BOOKKEEPING:
        return module_name
    for key in BOOKKEEPING:
        if module_name.endswith("\\" + key) or ("\\" + key + "\\") in module_name:
            return key
    return module_name


def inspect(data, formal=False):
    initializers, memories = [], []
    for module_name, module in data["modules"].items():
        for name, net in module.get("netnames", {}).items():
            if "init" not in net.get("attributes", {}):
                continue
            base = module_base(module_name)
            expected = BOOKKEEPING_VALUE.get((base, name))
            if formal and expected is not None:
                if str(net["attributes"]["init"]) != expected:
                    raise ValueError(f"unexpected initialized state: {module_name}.{name}")
                initializers.append(
                    {"module": module_name, "name": name, "value": net["attributes"]["init"]}
                )
                continue
            allowed = name in BOOKKEEPING.get(base, set()) or (
                name.startswith("$formal$") and name.endswith("_EN")
            )
            if (
                not formal
                or not allowed
                or set(str(net["attributes"]["init"])) != {"0"}
            ):
                raise ValueError(f"unexpected initialized state: {module_name}.{name}")
            initializers.append(
                {
                    "module": module_name,
                    "name": name,
                    "value": net["attributes"]["init"],
                }
            )
        for name, cell in module.get("cells", {}).items():
            if cell["type"].startswith("$meminit"):
                raise ValueError(f"initialized memory: {module_name}.{name}")
            if cell["type"] == "$mem_v2":
                value = cell["parameters"]["INIT"]
                if (
                    not isinstance(value, str)
                    or not value
                    or set(value.lower()) != {"x"}
                ):
                    raise ValueError(
                        f"memory is not unconstrained: {module_name}.{name}"
                    )
                memories.append(
                    {"module": module_name, "name": name, "bits": len(value)}
                )
    return {"formal_bookkeeping": initializers, "uninitialized_memories": memories}


def audit(destination):
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results = {}
    sources = " ".join(f"src/{path.name}" for path in sorted((ROOT / "src").glob("v3_*.v")))
    modes = [(top, False) for top in TOPS] + [(top, True) for top in TOPS]
    for top, formal in modes:
        name = ("formal-" if formal else "rtl-") + top
        # Preserve memories and init attributes before synthesis optimizations.
        # -formal defines FORMAL, which is what the V3 proofs are written under.
        source = "read_verilog " + ("-formal " if formal else "") + f"-I src {sources}"
        command = (
            source
            + f"; hierarchy -top {top}; proc; memory_collect; write_json {destination / (name + '.json')}"
        )
        with (destination / (name + ".log")).open("w") as log:
            subprocess.run(
                ["yosys", "-Q", "-T", "-p", command],
                cwd=ROOT,
                stdout=log,
                stderr=subprocess.STDOUT,
                check=True,
            )
        results[name] = inspect(
            json.loads((destination / (name + ".json")).read_text()), formal
        )
    summary = {
        "passed": True,
        "results": results,
        "sources": {
            str(p.relative_to(ROOT)): checksum(p)
            for p in sorted((ROOT / "src").glob("*.v"))
        },
    }
    (destination / "initialization.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(audit(args.output), indent=2))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Initialization audit failed: {error}\n")


if __name__ == "__main__":
    main()
