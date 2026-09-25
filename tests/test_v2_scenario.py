from copy import deepcopy
import json

import pytest

from protocol_emulator.v2.model import Engine
from protocol_emulator.v2.peers import decode_uart
from protocol_emulator.v2.scenario import playback, replay, uart_demo, write_artifacts


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
