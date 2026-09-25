"""Versioned, bounded V2 simulation scenarios and deterministic replay.

External stimulus retains integer nanosecond timestamps. Capture records are
synchronized observations at cycle timestamps and are never substituted for
that stimulus. This runner models execution, not host SPI wire bandwidth.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from .capture import Sample
from .firmware import uart_rx
from .isa import assemble, decode
from .model import Device
from .peers import decode_uart
from .replay import OutputEvent, compile_schedule


MAX_CYCLES = 1_000_000


def firmware_hash(words):
    return hashlib.sha256(b"".join(word.to_bytes(3, "big") for word in words)).hexdigest()


def _integer(value, lower, upper, field):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"invalid {field}")
    return value


def validate(scenario, *, require_expected=True):
    """Validate the supported contract before simulation or file export."""
    try:
        if (scenario["schema"] != "protocol-emulator-scenario" or scenario["version"] != 1
                or scenario["host_version"] != 2 or scenario["isa_version"] != 2
                or scenario["clock_hz"] != 25_000_000 or scenario["program_words"] != 64):
            raise ValueError("unsupported scenario or hardware version")
        _integer(scenario["seed"], 0, 0xFFFFFFFF, "seed")
        end = _integer(scenario["end_cycle"], 1, MAX_CYCLES - 1, "end cycle")
        config, initial = scenario["configuration"], scenario["initial_state"]
        _integer(config["start_mask"], 1, 3, "start mask")
        inputs = _integer(initial["inputs"], 0, 255, "initial inputs")
        if initial != {"inputs": inputs, "synchronizers": [inputs, inputs],
                       "engines": "reset", "outputs": 0, "enables": 0}:
            raise ValueError("initial state requires reset engines and settled inputs")
        device = Device(capacity=64)
        for mask in (*config["owners"], *config["open_drain"]):
            _integer(mask, 0, 255, "pin mask")
        device.configure(config["owners"], config["open_drain"])
        owned = config["owners"][0] | config["owners"][1]
        if len(scenario["firmware"]) != 2 or len(scenario["tx"]) != 2:
            raise ValueError("two firmware images and TX queues required")
        for engine, image, tx in zip(device.engines, scenario["firmware"], scenario["tx"]):
            if not isinstance(image["words"], list):
                raise ValueError("firmware word list required")
            for word in image["words"]:
                _integer(word, 0, 0xFFFFFF, "firmware word")
            engine.load(image["words"])
            if image["sha256"] != firmware_hash(image["words"]):
                raise ValueError("firmware hash mismatch")
            if not isinstance(tx, list) or len(tx) > 16:
                raise ValueError("invalid initial TX queue")
            for byte in tx:
                _integer(byte, 0, 255, "TX byte")
        previous = -1
        for edge in scenario["external_stimulus"]:
            time = _integer(edge["time_ns"], 0, end * 40, "external edge time")
            _integer(edge["inputs"], 0, 255, "external inputs")
            if (edge["inputs"] ^ inputs) & owned:
                raise ValueError("external stimulus may change only unowned pins")
            if time <= previous:
                raise ValueError("external edges must have increasing timestamps")
            previous = time
        trigger = scenario["capture"]
        device.capture.arm(Sample(inputs, 0, 0), trigger["mode"], trigger["mask"])
        decoder = scenario["decoder"]
        if decoder["protocol"] != "uart8n1":
            raise ValueError("unsupported scenario decoder")
        _integer(decoder["pin"], 0, 7, "decoder pin")
        if not owned & (1 << decoder["pin"]):
            raise ValueError("scenario decoder requires an owned output pin")
        _integer(decoder["period_ns"], 800, 2_400_000, "decoder period")
        for fault in scenario["fault_schedule"]:
            if fault["kind"] != "uart_stop_low":
                raise ValueError("unsupported fault recipe")
            _integer(fault["engine"], 0, 1, "fault engine")
            start = _integer(fault["cycle"], 0, end, "fault cycle")
            duration = _integer(fault["duration_cycles"], 1, end + 1, "fault duration")
            if start + duration > end + 1:
                raise ValueError("fault outside scenario window")
        if require_expected:
            capture = scenario["expected"]["capture"]
            if not capture["complete"] or capture["truncated"] or capture["reason"] != "stopped":
                raise ValueError("incomplete capture cannot be a regression source")
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError("malformed scenario") from error


def simulate(scenario):
    validate(scenario, require_expected=False)
    device = Device(capacity=64)
    config = scenario["configuration"]
    device.configure(config["owners"], config["open_drain"])
    external = scenario["initial_state"]["inputs"]
    pipeline = list(scenario["initial_state"]["synchronizers"])
    for engine, image, tx in zip(device.engines, scenario["firmware"], scenario["tx"]):
        engine.load(image["words"])
        engine.push(tx)
        engine.previous_pins = external
    device.start(config["start_mask"])
    stimulus = iter(scenario["external_stimulus"])
    next_edge = next(stimulus, None)
    outputs, levels = [], []
    for cycle in range(scenario["end_cycle"] + 1):
        # Edges exactly on a clock boundary are applied before its sampling.
        while next_edge is not None and next_edge["time_ns"] <= cycle * 40:
            external = next_edge["inputs"]
            next_edge = next(stimulus, None)
        old_out, old_oe = device.pins
        bus = (external & ~old_oe) | (old_out & old_oe)
        pins = pipeline.pop(0)
        pipeline.append(bus)
        capture_arm = (scenario["capture"]["mode"], scenario["capture"]["mask"]) if cycle == 0 else None
        out, oe = device.tick(pins, capture_arm=capture_arm)
        if not outputs or (out, oe) != (outputs[-1].outputs, outputs[-1].enables):
            outputs.append(OutputEvent(cycle, out, oe))
        level = (external & ~oe) | (out & oe)
        if not levels or levels[-1][1] != level:
            levels.append((cycle * 40, level))
    device.capture.stop()
    capture = device.capture.export()  # no incomplete regression exports
    decoder = scenario["decoder"]
    result = {"capture": capture, "outputs": [asdict(event) for event in outputs],
            "rx": [[list(item) for item in engine.rx] for engine in device.engines],
            "errors": [engine.errors for engine in device.engines],
            "decoded": decode_uart(levels, end_ns=(scenario["end_cycle"] + 1) * 40,
                                   period_ns=decoder["period_ns"], pin=decoder["pin"],
                                   initial=scenario["initial_state"]["inputs"])}
    faults = scenario["fault_schedule"]
    failed = [frame for frame in result["decoded"] if frame["framing_error"]]
    if len(failed) != len(faults):
        raise ValueError("decoded framing errors do not match the fault schedule")
    for frame, fault in zip(failed, faults):
        if (frame["start_ns"] + 9 * decoder["period_ns"] != fault["cycle"] * 40
                or fault["duration_cycles"] * 40 != decoder["period_ns"]
                or not config["owners"][fault["engine"]] & (1 << decoder["pin"])):
            raise ValueError("fault timing or ownership differs from the waveform")
    return result


def replay(scenario):
    validate(scenario)
    actual = simulate(scenario)
    if actual != scenario["expected"]:
        # Keep the first differing top-level result and both values inspectable.
        key = next((key for key in actual if actual[key] != scenario["expected"].get(key)), "schema")
        raise ValueError(f"replay divergence in {key}: expected={scenario['expected'].get(key)!r}, actual={actual.get(key)!r}")
    return actual


def playback(scenario):
    """Compile the verified scenario's aggregate output waveform."""
    result = replay(scenario)
    config = scenario["configuration"]
    return compile_schedule([OutputEvent(**event) for event in result["outputs"]],
                            end_cycle=scenario["end_cycle"],
                            owner=config["owners"][0] | config["owners"][1],
                            open_drain=config["open_drain"][0] | config["open_drain"][1])


def uart_demo(*, bad_stop=True, period=217, value=0xA5, seed=0xD3B6):
    """Single scheduled framing fault plus independent asynchronous RX traffic."""
    if period not in (25, 217) or type(bad_stop) is not bool:
        raise ValueError("unsupported UART demo")
    _integer(value, 0, 255, "UART value")
    _integer(seed, 0, 0xFFFFFFFF, "seed")
    start = 32
    lines = ["DRIVE 1, 1", "PULL 0", "MOVOS 0", f"DELAY {start - 3}",
             "DRIVE 0, 1", f"DELAY {period - 1}"]
    for bit in range(8):
        lines += ["OUT 0, 0", f"DELAY {period - (2 if bit == 7 else 1)}"]
    lines += ["MARK 1", f"DRIVE {0 if bad_stop else 1}, 1", f"DELAY {period - 1}",
              "DRIVE 1, 1", "HALT"]
    words = [assemble("\n".join(lines), capacity=64), assemble(uart_rx(period), capacity=64)]
    incoming = seed % 256
    edges = []
    # Phase 13 ns is external source information; preserve it in JSON.
    for bit, level in enumerate([0] + [incoming >> bit & 1 for bit in range(8)] + [1]):
        edges.append({"time_ns": 213 + bit * period * 40, "inputs": 253 | level << 1})
    scenario = {"schema": "protocol-emulator-scenario", "version": 1,
                "clock_hz": 25_000_000, "host_version": 2, "isa_version": 2,
                "program_words": 64, "seed": seed, "end_cycle": start + 12 * period,
                "configuration": {"owners": [1, 0], "open_drain": [0, 0], "start_mask": 3},
                "initial_state": {"inputs": 255, "synchronizers": [255, 255],
                                  "engines": "reset", "outputs": 0, "enables": 0},
                "firmware": [{"words": image, "sha256": firmware_hash(image)} for image in words],
                "tx": [[value], []], "external_stimulus": edges,
                "fault_schedule": ([{"kind": "uart_stop_low", "engine": 0,
                                     "cycle": start + 9 * period, "duration_cycles": period}]
                                   if bad_stop else []),
                "capture": {"mode": "marker", "mask": 1},
                "decoder": {"protocol": "uart8n1", "period_ns": period * 40, "pin": 0}}
    result = simulate(scenario)
    if (result["decoded"] != [{"start_ns": start * 40, "value": value, "framing_error": bad_stop}]
            or result["rx"] != [[], [[incoming, 0]]] or result["errors"] != [0, 0]):
        raise ValueError("UART demonstration failed its independent expected result")
    scenario["expected"] = result
    validate(scenario)
    return scenario


def write_artifacts(scenario, output):
    replay(scenario)  # Validate before creating either artifact.
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(scenario, indent=2, sort_keys=True) + "\n")
    # The capture model already defines the waveform-view serialization.
    from .capture import Capture, Record
    capture = Capture()
    data = scenario["expected"]["capture"]
    capture.records = [Record(**record) for record in data["records"]]
    capture.cycle, capture.trigger_cycle = data["end_cycle"], data["trigger_cycle"]
    capture.triggered, capture.reason = True, "stopped"
    output.with_suffix(".vcd").write_text(capture.vcd())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("uart-demo")
    demo.add_argument("output", type=Path)
    demo.add_argument("--good-stop", action="store_true")
    demo.add_argument("--period", type=int, choices=(25, 217), default=217)
    for command in ("replay", "playback"):
        sub = commands.add_parser(command)
        sub.add_argument("scenario", type=Path)
    args = parser.parse_args()
    if args.command == "uart-demo":
        scenario = uart_demo(bad_stop=not args.good_stop, period=args.period)
        write_artifacts(scenario, args.output)
        print(f"Wrote {args.output.with_suffix('.json')} and {args.output.with_suffix('.vcd')}")
    else:
        scenario = json.loads(args.scenario.read_text())
        if args.command == "replay":
            print(json.dumps(replay(scenario)["decoded"], sort_keys=True))
        else:
            for word in playback(scenario):
                instruction = decode(word)
                print(instruction.name + (" " if instruction.operands else "")
                      + ", ".join(map(str, instruction.operands)))


if __name__ == "__main__":
    main()
