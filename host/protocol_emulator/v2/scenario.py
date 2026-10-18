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
from .peers import decode_uart, decode_spi, decode_i2c
from .replay import OutputEvent, compile_schedule


MAX_CYCLES = 1_000_000


def firmware_hash(words):
    return hashlib.sha256(b"".join(word.to_bytes(3, "big") for word in words)).hexdigest()


def _integer(value, lower, upper, field):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"invalid {field}")
    return value


def validate(scenario, *, require_expected=True):
    """Validate the supported scenario format before simulation or file export."""
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
            if (edge["inputs"] ^ inputs) & owned & ~(config["open_drain"][0] | config["open_drain"][1]):
                raise ValueError("external stimulus may change only unowned or open-drain pins")
            if time <= previous:
                raise ValueError("external edges must have increasing timestamps")
            previous = time
        trigger = scenario["capture"]
        device.capture.arm(Sample(inputs, 0, 0), trigger["mode"], trigger["mask"])
        decoder = scenario["decoder"]
        protocol = decoder["protocol"]
        if protocol not in ("uart8n1", "spi", "i2c"):
            raise ValueError("unsupported scenario decoder")
        if protocol == "uart8n1":
            _integer(decoder["pin"], 0, 7, "decoder pin")
            if not owned & (1 << decoder["pin"]):
                raise ValueError("scenario decoder requires an owned output pin")
            _integer(decoder["period_ns"], 800, 2_400_000, "decoder period")
        elif protocol == "spi":
            _integer(decoder["mode"], 0, 3, "SPI mode")
            _integer(decoder["expected_value"], 0, 255, "SPI expected byte")
            if owned != 8 or any(config["open_drain"]):
                raise ValueError("SPI scenario requires MISO-only ownership")
        elif owned != 1 or config["open_drain"] != config["owners"]:
            raise ValueError("I2C scenario requires open-drain SDA ownership")
        for fault in scenario["fault_schedule"]:
            if fault["kind"] != {"uart8n1": "uart_stop_low", "spi": "spi_response_delay", "i2c": "i2c_nack"}[protocol]:
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
            "decoded": []}
    initial = scenario["initial_state"]["inputs"]
    protocol = decoder["protocol"]
    if protocol == "uart8n1":
        result["decoded"] = decode_uart(levels, end_ns=(scenario["end_cycle"] + 1) * 40,
                                        period_ns=decoder["period_ns"], pin=decoder["pin"], initial=initial)
    elif protocol == "spi":
        result["decoded"] = decode_spi(levels, mode=decoder["mode"], initial=initial)
    else:
        result["decoded"] = decode_i2c(levels, initial=initial)
    faults = scenario["fault_schedule"]
    if protocol != "uart8n1":
        failed = [frame for frame in result["decoded"] if
                  (frame["value"] != decoder["expected_value"] if protocol == "spi" else not frame["ack"])]
        if len(failed) != len(faults):
            raise ValueError("decoded protocol errors do not match the fault schedule")
        for frame, fault in zip(failed, faults):
            if not config["owners"][fault["engine"]]:
                raise ValueError("fault has no output ownership")
            if protocol == "i2c" and not fault["cycle"] * 40 <= frame["ack_ns"] < (fault["cycle"] + fault["duration_cycles"]) * 40:
                raise ValueError("NACK outside scheduled fault window")
            if protocol == "spi":
                edge = (fault["cycle"] + fault["duration_cycles"]) * 40
                if not any(event.cycle * 40 == edge and event.outputs & 8 for event in outputs):
                    raise ValueError("delayed response does not match fault timing")
        return result
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


def prepare_hardware(device, scenario):
    """Validate, load/readback and arm a controlled scenario through the V2 API.

    The caller must arrange the recorded external stimulus relative to the
    physical running indication, then call device.start(start_mask). SPI host
    transaction completion is not the execution epoch and provides no precise
    timing trigger for a software-only external stimulus source.
    """
    replay(scenario)  # Reject invalid artifacts before any device operation.
    if scenario["capture"]["mode"] == "immediate":
        raise ValueError("hardware scenario requires an execution-relative trigger")
    device.check_version()
    for engine in range(2):
        device.stop(engine)
        device.reset(engine)
    for engine in range(2):
        device.configure(engine, 0, 0)
    device.configure_filter(0)
    config = scenario["configuration"]
    for engine, image in enumerate(scenario["firmware"]):
        device.configure(engine, config["owners"][engine], config["open_drain"][engine])
        device.load(engine, image["words"])
        device.fifo_write(engine, scenario["tx"][engine])
    device.capture_arm(scenario["capture"]["mode"], scenario["capture"]["mask"])
    return config["start_mask"]


def collect_hardware(device, scenario):
    """Freeze/read capture and compare every event relative to its trigger.

    Call after the controlled peer has finished the recorded window. The host
    stop latency can extend the quiet tail, but no changed event may be omitted.
    This checks synchronized observations, not analog timing or an independent
    peer's raw-pin decoder; pin-level acceptance checks both separately.
    """
    replay(scenario)
    device.capture_stop()
    actual = device.capture_read()
    expected = scenario["expected"]["capture"]
    normalize = lambda trace: [dict(r, cycle=r["cycle"] - trace["trigger_cycle"]) for r in trace["records"]]
    if not actual["complete"] or actual["truncated"] or normalize(actual) != normalize(expected):
        raise ValueError("hardware capture differs from scenario")
    received = [device.fifo_read(engine, len(records))
                for engine, records in enumerate(scenario["expected"]["rx"])]
    if [[list(record) for record in records] for records in received] != scenario["expected"]["rx"]:
        raise ValueError("hardware RX differs from scenario")
    errors = [device.status(engine) >> 1 for engine in range(2)]
    if errors != scenario["expected"]["errors"]:
        raise ValueError("hardware errors differ from scenario")
    return {"capture": actual, "rx": received, "errors": errors}


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


def _scheduled_demo(events, edges, *, owner, open_drain, initial, end, decoder, fault, capture):
    # Leave three cycles for release and synchronized feedback to enter capture.
    words = [compile_schedule(events, end_cycle=end - 4, owner=owner, open_drain=open_drain),
             assemble("HALT", capacity=64)]
    scenario = {"schema": "protocol-emulator-scenario", "version": 1,
                "clock_hz": 25_000_000, "host_version": 2, "isa_version": 2,
                "program_words": 64, "seed": 0, "end_cycle": end,
                "configuration": {"owners": [owner, 0], "open_drain": [open_drain, 0], "start_mask": 1},
                "initial_state": {"inputs": initial, "synchronizers": [initial, initial],
                                  "engines": "reset", "outputs": 0, "enables": 0},
                "firmware": [{"words": image, "sha256": firmware_hash(image)} for image in words],
                "tx": [[], []], "external_stimulus": edges, "fault_schedule": fault,
                "capture": capture, "decoder": decoder}
    scenario["expected"] = simulate(scenario)
    validate(scenario)
    return scenario


def spi_demo(*, delayed=True):
    """A scheduled MISO response misses the first sample of a mode-0 byte.

    External SCK is exactly 1 MHz, offset 13 ns from the system clock. The
    recorded controller stimulus is replayable; the firmware is a bounded
    response schedule, not a replacement for the general SPI target library.
    """
    if type(delayed) is not bool:
        raise ValueError("invalid delayed response selection")
    expected = 0x80
    events = [OutputEvent(0, 0, 8)]
    last = 0
    for bit in range(8):
        value = (expected >> (7 - bit) & 1) << 3
        if value != last:
            events.append(OutputEvent(55 if bit == 0 and delayed else 40 + bit * 25, value, 8))
            last = value
    edges = [{"time_ns": 1213, "inputs": 1}]
    for bit in range(8):
        edges += [{"time_ns": 2013 + bit * 1000, "inputs": 3},
                  {"time_ns": 2513 + bit * 1000, "inputs": 1}]
    edges.append({"time_ns": 10013, "inputs": 5})
    scenario = _scheduled_demo(events, edges, owner=8, open_drain=0, initial=5, end=280,
        decoder={"protocol": "spi", "mode": 0, "expected_value": expected},
        capture={"mode": "pin", "mask": 4},
        fault=([{"kind": "spi_response_delay", "engine": 0, "cycle": 40, "duration_cycles": 15}]
               if delayed else []))
    if [frame["value"] for frame in scenario["expected"]["decoded"]] != [0 if delayed else expected]:
        raise ValueError("SPI demonstration failed independent decoding")
    return scenario


def i2c_demo(*, nack=True):
    """Scheduled address NACK versus ACK on a recorded open-drain transaction."""
    if type(nack) is not bool:
        raise ValueError("invalid NACK selection")
    transitions = {30: 2}
    for bit in range(8):
        sda = 0x84 >> (7 - bit) & 1
        transitions[60 + bit * 63] = sda
        transitions[93 + bit * 63] = sda | 2
        transitions[123 + bit * 63] = sda
    transitions.update({564: 1, 597: 3, 627: 1, 650: 0, 683: 2, 715: 3})
    edges = [{"time_ns": cycle * 40 + 13, "inputs": value} for cycle, value in sorted(transitions.items())]
    events = [OutputEvent(0, 0, 0)]
    if not nack:
        events += [OutputEvent(580, 0, 1), OutputEvent(635, 0, 0)]
    scenario = _scheduled_demo(events, edges, owner=1, open_drain=1, initial=3, end=750,
        decoder={"protocol": "i2c"},
        capture={"mode": "pin", "mask": 1},
        fault=([{"kind": "i2c_nack", "engine": 0, "cycle": 580, "duration_cycles": 55}] if nack else []))
    decoded = scenario["expected"]["decoded"]
    if len(decoded) != 1 or decoded[0]["value"] != 0x84 or decoded[0]["ack"] == nack:
        raise ValueError("I2C demonstration failed independent decoding")
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
    for name in ("spi-demo", "i2c-demo"):
        demo = commands.add_parser(name)
        demo.add_argument("output", type=Path)
        demo.add_argument("--without-fault", action="store_true")
    for command in ("replay", "playback"):
        sub = commands.add_parser(command)
        sub.add_argument("scenario", type=Path)
    args = parser.parse_args()
    if args.command.endswith("-demo"):
        if args.command == "uart-demo":
            scenario = uart_demo(bad_stop=not args.good_stop, period=args.period)
        elif args.command == "spi-demo":
            scenario = spi_demo(delayed=not args.without_fault)
        else:
            scenario = i2c_demo(nack=not args.without_fault)
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
