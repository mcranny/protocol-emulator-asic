from copy import deepcopy
import json

import pytest

from protocol_emulator.v2.model import Engine
from protocol_emulator.v2.peers import decode_uart
from protocol_emulator.v2.scenario import (playback, replay, uart_demo, spi_demo, i2c_demo,
                                          write_artifacts, prepare_hardware, collect_hardware)


@pytest.mark.parametrize("period", [25, 217])
@pytest.mark.parametrize("bad_stop", [False, True])
def test_fault_demo_export_replay_and_ordinary_firmware_playback(tmp_path, period, bad_stop):
    scenario = uart_demo(period=period, bad_stop=bad_stop)
    output = tmp_path / "demo"
    write_artifacts(scenario, output)
    loaded = json.loads(output.with_suffix(".json").read_text())
    assert loaded == scenario
    assert loaded["external_stimulus"][0]["time_ns"] % 40 == 13
    result = replay(loaded)
    assert result["decoded"] == [{"start_ns": 1280, "value": 0xA5, "framing_error": bad_stop}]
    assert result["rx"] == [[], [[0xB6, 0]]]
    assert result["capture"]["complete"] and not result["capture"]["truncated"]
    records = result["capture"]["records"]
    stop = 32 + 9 * period
    assert result["capture"]["trigger_cycle"] == stop
    assert records[0]["flags"] & 9 == 9
    # The stop instruction executes at stop, captured before the next edge.
    after_stop = next(record for record in records if record["cycle"] == stop + 1)
    assert after_stop["outputs"] & 1 == int(not bad_stop)
    vcd = output.with_suffix(".vcd").read_text()
    assert f"#{stop * 40}\n" in vcd and "complete=True" in vcd
    engine = Engine(capacity=64)
    engine.owner = 1
    engine.load(playback(loaded))
    engine.start()
    index = 0
    for cycle in range(scenario["end_cycle"] + 1):
        events = result["outputs"]
        if index + 1 < len(events) and events[index + 1]["cycle"] == cycle:
            index += 1
        engine.tick()
        out, oe = engine.pins
        assert (out & oe, oe) == (events[index]["outputs"], events[index]["enables"]), cycle


def test_removing_fault_changes_framing_only_and_replay_detects_shifted_capture():
    bad, good = uart_demo(), uart_demo(bad_stop=False)
    assert bad["expected"]["decoded"][0]["framing_error"]
    assert not good["expected"]["decoded"][0]["framing_error"]
    assert bad["expected"]["rx"] == good["expected"]["rx"]
    assert bad["firmware"][0]["sha256"] != good["firmware"][0]["sha256"]
    bad["expected"]["capture"]["records"][1]["cycle"] += 1
    with pytest.raises(ValueError, match="divergence in capture"):
        replay(bad)


@pytest.mark.parametrize("change,match", [
    (lambda s: s.update(version=2), "unsupported"),
    (lambda s: s.update(isa_version=1), "unsupported"),
    (lambda s: s["firmware"][0]["words"].__setitem__(0, 0), "hash"),
    (lambda s: s["expected"]["capture"].update(complete=False), "incomplete"),
    (lambda s: s["expected"]["capture"].update(truncated=True), "incomplete"),
    (lambda s: s["initial_state"].update(synchronizers=[0, 0]), "initial state"),
    (lambda s: s["external_stimulus"][0].update(time_ns=213.5), "edge time"),
    (lambda s: s["external_stimulus"][1].update(time_ns=213), "increasing"),
    (lambda s: s["external_stimulus"][0].update(inputs=252), "unowned"),
    (lambda s: s["configuration"].update(owners=[1, 1]), "ownership"),
    (lambda s: s["fault_schedule"][0].update(cycle=1986), "fault timing"),
    (lambda s: s.update(fault_schedule=[]), "fault schedule"),
    (lambda s: s.update(end_cycle=1_000_000), "end cycle"),
])
def test_invalid_or_incomplete_regressions_rejected_before_export(tmp_path, change, match):
    scenario = deepcopy(uart_demo())
    change(scenario)
    with pytest.raises(ValueError, match=match):
        write_artifacts(scenario, tmp_path / "rejected")
    assert not list(tmp_path.iterdir())


def test_independent_decoder_false_start_and_truncation():
    assert decode_uart([(10, 0), (12, 1)], end_ns=1000, period_ns=100) == []
    with pytest.raises(ValueError, match="incomplete"):
        decode_uart([(10, 0)], end_ns=900, period_ns=100)


@pytest.mark.parametrize('protocol', ['spi', 'i2c'])
@pytest.mark.parametrize('fault', [False, True])
def test_spi_i2c_fault_export_replay_and_playback(tmp_path, protocol, fault):
    scenario = spi_demo(delayed=fault) if protocol == 'spi' else i2c_demo(nack=fault)
    write_artifacts(scenario, tmp_path / protocol)
    reloaded = json.loads((tmp_path / f'{protocol}.json').read_text())
    result = replay(reloaded)
    frame, = result['decoded']
    if protocol == 'spi': assert frame['value'] == (0 if fault else 128)
    else: assert frame['value'] == 0x84 and frame['ack'] is not fault
    assert len(result['capture']['records']) < 32
    assert result['capture']['complete'] and not result['capture']['truncated']
    engine = Engine(capacity=64)
    engine.owner = scenario['configuration']['owners'][0]
    engine.open_drain = scenario['configuration']['open_drain'][0]
    engine.load(playback(scenario))
    engine.start()
    events = iter(result['outputs'])
    expected = next(events)
    upcoming = next(events, None)
    for cycle in range(scenario['end_cycle'] + 1):
        if upcoming and upcoming['cycle'] == cycle:
            expected, upcoming = upcoming, next(events, None)
        engine.tick()
        value, enable = engine.pins
        assert (value & enable, enable) == (expected['outputs'], expected['enables'])
    if fault:
        scenario['fault_schedule'][0]['cycle'] += 100
        with pytest.raises(ValueError, match='timing|window'):
            replay(scenario)


def test_hardware_workflow_validation_and_epoch_alignment():
    scenario = spi_demo()
    class Device:
        def __init__(self): self.calls = []
        def __getattr__(self, name):
            def method(*args):
                self.calls.append((name, args))
                if name == 'capture_read':
                    capture = deepcopy(scenario['expected']['capture'])
                    capture['trigger_cycle'] += 9000
                    capture['end_cycle'] += 20000
                    for record in capture['records']: record['cycle'] += 9000
                    return capture
                if name == 'fifo_read': return []
                if name == 'status': return 0
            return method
    device = Device()
    assert prepare_hardware(device, scenario) == 1
    assert device.calls[-1] == ('capture_arm', ('pin', 4))
    assert [name for name, _ in device.calls].count('load') == 2
    assert collect_hardware(device, scenario)['errors'] == [0, 0]
    scenario['firmware'][0]['sha256'] = 'bad'
    device.calls = []
    with pytest.raises(ValueError, match='hash'):
        prepare_hardware(device, scenario)
    assert device.calls == []
