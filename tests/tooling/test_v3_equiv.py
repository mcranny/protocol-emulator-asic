from pathlib import Path

import pytest

from tools.v3_equiv import TOP, config, script, sources, summarize

ROOT = Path(__file__).resolve().parents[2]


def test_sources_reads_the_repository_info_yaml():
    files = sources(ROOT / "info.yaml")
    assert files and all(name.endswith(".v") for name in files)
    assert all((ROOT / "src" / name).is_file() for name in files)


def test_sources_rejects_another_top(tmp_path: Path):
    info = tmp_path / "info.yaml"
    info.write_text("project:\n  top_module: other\n  source_files: [a.v]\n")
    with pytest.raises(ValueError, match="top module"):
        sources(info)


def test_sources_rejects_paths(tmp_path: Path):
    info = tmp_path / "info.yaml"
    info.write_text(f"project:\n  top_module: {TOP}\n  source_files: [sub/a.v]\n")
    with pytest.raises(ValueError, match="plain Verilog"):
        sources(info)


def test_config_black_boxes_the_sram_on_both_sides_and_unpairs_its_nets():
    text = config(["a.v", "b.v"], "/src", "/netlist/n.v", "/pdk/cells.lib")
    gold, gate = text.split("[gate]")
    assert "read_verilog -DV3_SRAM_BLACKBOX -I/src /src/a.v /src/b.v" in gold
    assert "read_verilog -DV3_SRAM_BLACKBOX /src/v3_sram_blackbox.vh" in gate
    assert "read_liberty -ignore_miss_func /pdk/cells.lib" in gate
    assert "gold-nomatch u_progmem*.a_*" in text
    assert "use sat" in text


def test_config_pairs_the_merged_filter_samples_with_the_synchronizer():
    text = config(["a.v"], "/src", "/netlist/n.v", "/pdk/cells.lib")
    assert r"gold-match /u_filter\.previous\[(\d+)\]/ /cap_sync0\[\1\]/" in text
    assert r"gold-match /u_filter\.older\[(\d+)\]/ /cap_sync1\[\1\]/" in text
    assert text.index("gold-nomatch") < text.index("gold-match")


def test_config_leaves_only_ports_and_flop_outputs_named():
    text = config(["a.v"], "/src", "/netlist/n.v", "/pdk/cells.lib")
    gold, gate = text.split("[gate]")
    assert ("opt_clean\nflatten\nrename -hide w:* t:$*dff* %co1:+[Q] w:* %i %d "
            "t:$*dff* %ci1:+[CLK] w:* %i %d w:clknet_* %d x:* %d") in gold
    assert ("rename -hide w:* t:sg13cmos5l_df* %co1:+[Q] w:* %i %d "
            "t:sg13cmos5l_df* %ci1:+[CLK] w:* %i %d w:clknet_* %d x:* %d") in gate


def test_script_sets_up_then_runs_each_partition():
    command = script(3)
    assert command.startswith("eqy -m ")
    assert "xargs -P 3" in command and "source run.sh" in command
    assert "make" not in command


def _partition(root: Path, name: str, status: str | None):
    run = root / name / "sat"
    run.mkdir(parents=True)
    (run / "run.sh").write_text("")
    if status is not None:
        (run / "status").write_text(status + "\n")


def test_summarize_passes_only_when_every_partition_passed(tmp_path: Path):
    _partition(tmp_path, "top.a", "PASS")
    _partition(tmp_path, "top.b", "PASS")
    summary = summarize(tmp_path)
    assert summary["status"] == "PASS" and summary["partition_count"] == 2
    assert summary["failed"] == []


def test_summarize_reports_failed_unknown_and_missing(tmp_path: Path):
    _partition(tmp_path, "top.a", "PASS")
    _partition(tmp_path, "top.b", "FAIL")
    _partition(tmp_path, "top.c", "UNKNOWN")
    _partition(tmp_path, "top.d", None)
    summary = summarize(tmp_path)
    assert summary["status"] == "FAIL"
    assert summary["failed"] == ["top.b", "top.c", "top.d"]
    assert summary["partitions"]["top.d"] == "NOT RUN"


def test_summarize_fails_with_no_partitions(tmp_path: Path):
    assert summarize(tmp_path)["status"] == "FAIL"
