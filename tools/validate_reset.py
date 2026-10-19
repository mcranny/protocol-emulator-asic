"""Run four-state reset/state-loss checks without initializing DUT storage."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from tools.check_physical import checksum, passing_xml
from tools.local_flow import PDK_REV, verify_checkout
from tools.validate_local import source_state

ROOT = Path(__file__).resolve().parents[1]


def environment():
    env = os.environ.copy()
    for name in (
        "COCOTB_TEST_FILTER",
        "COCOTB_TESTCASE",
        "TESTCASE",
        "GATES",
        "COMPILE_ARGS",
        "EXTRA_ARGS",
        "SIM_ARGS",
        "PLUSARGS",
    ):
        env.pop(name, None)
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
    env["COCOTB_RESOLVE_X"] = "error"
    env["COCOTB_RANDOM_SEED"] = "20260926"
    return env


def validate(destination, versions=(1, 2), netlist=None, pdk_root=None):
    if not versions or len(set(versions)) != len(versions) or set(versions) - {1, 2}:
        raise ValueError("select V1 and/or V2 exactly once")
    if (netlist is None) != (pdk_root is None) or (netlist and len(versions) != 1):
        raise ValueError("gate-level checks require one version, netlist, and PDK root")
    destination = Path(destination).resolve()
    before = source_state(ROOT)
    manifest = dict(
        before,
        schema=1,
        simulation="gatelevel" if netlist else "rtl",
        passed=False,
        commands=[],
        reports={},
    )
    if netlist:
        netlist, pdk_root = Path(netlist).resolve(), Path(pdk_root).resolve()
        verify_checkout(pdk_root, PDK_REV)
        model_root = pdk_root / "ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog"
        manifest.update(
            netlist_sha256=checksum(netlist),
            pdk_revision=PDK_REV,
            cell_models={
                name: checksum(model_root / name)
                for name in ("sg13cmos5l_udp.v", "sg13cmos5l_stdcell.v")
            },
        )
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for version in versions:
            name = f"v{version}"
            env = environment()
            report = destination / (name + ".json")
            env["RESET_AUDIT_REPORT"] = str(report)
            xml = destination / (name + ".xml")
            command = [
                "make",
                "-C",
                "test",
                "-f",
                "Makefile.reset",
                f"VERSION={version}",
                f"SIM_BUILD={destination / ('sim-' + name)}",
                f"COCOTB_RESULTS_FILE={xml}",
            ]
            if netlist:
                command += [
                    "GATES=yes",
                    f"GATE_LEVEL_NETLIST={netlist}",
                    f"PDK_ROOT={pdk_root}",
                ]
            with (destination / (name + ".log")).open("w") as log:
                result = subprocess.run(
                    command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
                )
            manifest["commands"].append(
                {"argv": command, "exit_code": result.returncode}
            )
            if result.returncode:
                raise ValueError(f"{name} reset audit failed; see retained log")
            passing_xml(xml, required_names={f"test_{name}_reset_and_state_loss"})
            data = json.loads(report.read_text())
            required = {
                "startup_reset_reload",
                "reset_during_activity",
                "powered_disable_and_reselection",
                "state_loss_reset_reconfigure_reload",
            }
            if (
                data.get("passed") is not True
                or data.get("resolve_x") != "error"
                or set(data.get("checks", [])) != required
                or len(data["checks"]) != 4
                or any(
                    "X" not in data.get(key, "").upper()
                    for key in (
                        "startup_unknown",
                        "replacement_unknown",
                        "replacement_unknown_before_reset",
                    )
                )
            ):
                raise ValueError(f"{name} lacks required four-state evidence")
            manifest["reports"][name] = data
        if source_state(ROOT) != before:
            raise ValueError("source changed during reset audit")
        if netlist and checksum(netlist) != manifest["netlist_sha256"]:
            raise ValueError("netlist changed during reset audit")
        manifest["passed"] = True
    finally:
        manifest["artifacts"] = {
            p.name: checksum(p) for p in destination.iterdir() if p.is_file()
        }
        (destination / "audit.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n"
        )
    return manifest


def check_record(record, versions, identity, simulation, netlist_hash=None):
    """Validate an audit's identity and every required four-state observation."""
    if (
        record.get("passed") is not True
        or record.get("simulation") != simulation
        or any(
            record.get(k) != identity[k]
            for k in ("source_commit", "source_dirty", "inputs")
        )
        or len(record.get("commands", [])) != len(versions)
        or any(command.get("exit_code") != 0 for command in record["commands"])
    ):
        raise ValueError("reset audit is missing, failed, or from different source")
    if netlist_hash is not None and record.get("netlist_sha256") != netlist_hash:
        raise ValueError("reset audit netlist differs")
    reports = record.get("reports", {})
    if set(reports) != {f"v{v}" for v in versions}:
        raise ValueError("reset audit versions differ")
    for version in versions:
        data = reports[f"v{version}"]
        required = {
            "startup_reset_reload",
            "reset_during_activity",
            "powered_disable_and_reselection",
            "state_loss_reset_reconfigure_reload",
        }
        if (
            data.get("version") != version
            or data.get("passed") is not True
            or data.get("simulator") != "Icarus Verilog"
            or data.get("resolve_x") != "error"
            or set(data.get("checks", [])) != required
            or len(data["checks"]) != 4
            or any(
                "X" not in data.get(key, "").upper()
                for key in (
                    "startup_unknown",
                    "replacement_unknown",
                    "replacement_unknown_before_reset",
                )
            )
        ):
            raise ValueError("reset audit lacks required four-state observations")


def check_initialization(summary, inputs):
    if summary.get("passed") is not True:
        raise ValueError("initialization audit failed or missing")
    required_sources = {
        "src/engine.v",
        "src/host_spi.v",
        "src/project.v",
        "src/v2_engine.v",
        "src/v2_capture.v",
        "src/v2_input_filter.v",
        "src/v2_project.v",
    }
    sources = summary.get("sources", {})
    if not required_sources <= sources.keys() or any(
        inputs.get(k) != v for k, v in sources.items()
    ):
        raise ValueError("initialization audit source differs")
    from tools.audit_initialization import BOOKKEEPING, TOPS

    expected = {"rtl-" + top for top in TOPS} | {"formal-" + top for top in BOOKKEEPING}
    if set(summary.get("results", {})) != expected:
        raise ValueError("initialization audit elaboration inventory differs")
    if any(
        summary["results"]["rtl-" + top].get("formal_bookkeeping") != [] for top in TOPS
    ):
        raise ValueError("synthesized state was initialized")

    memory_inventory = {
        "protocol_engine": {
            ("protocol_engine", "fifo", 32),
            ("protocol_engine", "program_mem", 1536),
        },
        "protocol_engine_v2": {
            ("protocol_engine_v2", "program_mem", 1536),
            ("protocol_engine_v2", "tx_mem", 128),
            ("protocol_engine_v2", "rx_mem", 256),
        },
        "protocol_capture_v2": {("protocol_capture_v2", "records", 2048)},
        "protocol_input_filter_v2": set(),
    }
    v2_modules = {
        "protocol_engine_v2",
        "protocol_capture_v2",
        "protocol_input_filter_v2",
        TOPS[1],
    }
    for name, result in summary["results"].items():
        formal, top = name.split("-", 1)
        modules = (
            v2_modules
            if top == TOPS[1]
            else {"protocol_engine"}
            if top == TOPS[0]
            else {top}
        )
        expected_memory = set().union(
            *(memory_inventory.get(module, set()) for module in modules)
        )
        memories = result.get("uninitialized_memories", [])
        actual_memory = {
            (m.get("module"), m.get("name"), m.get("bits")) for m in memories
        }
        if actual_memory != expected_memory or len(memories) != len(expected_memory):
            raise ValueError("initialization audit memory inventory differs")
        history = result.get("formal_bookkeeping", [])
        required_history = (
            {
                (module, signal)
                for module in modules
                for signal in BOOKKEEPING.get(module, set())
            }
            if formal == "formal"
            else set()
        )
        actual_history = set()
        seen = set()
        for entry in history:
            key = (entry.get("module"), entry.get("name"))
            generated_enable = (
                isinstance(key[1], str)
                and key[1].startswith("$formal$")
                and key[1].endswith("_EN")
            )
            if (
                key in seen
                or key[0] not in modules
                or set(str(entry.get("value", ""))) != {"0"}
                or (key not in required_history and not generated_enable)
            ):
                raise ValueError("initialization audit history inventory differs")
            seen.add(key)
            if not generated_enable:
                actual_history.add(key)
        if actual_history != required_history:
            raise ValueError("initialization audit history inventory differs")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", type=int, choices=(1, 2))
    parser.add_argument("--netlist", type=Path)
    parser.add_argument("--pdk-root", type=Path)
    args = parser.parse_args()
    try:
        result = validate(
            args.output,
            (args.version,) if args.version else (1, 2),
            args.netlist,
            args.pdk_root,
        )
        print(
            json.dumps(
                {k: result[k] for k in ("passed", "simulation", "reports")}, indent=2
            )
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Reset audit failed: {error}\n")


if __name__ == "__main__":
    main()
