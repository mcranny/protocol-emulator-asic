from pathlib import Path

import pytest

from tools.sim_stdcell import functional_view, main

CELL = """module dff (Q, CLK, D);
  output Q; input CLK, D;
  wire delayed_CLK, delayed_D;
  ihp_dff_r (Q, delayed_CLK, delayed_D, notifier);
  specify
    (posedge CLK => (Q +: D)) = (0.1, 0.1);
    $setuphold(posedge CLK, D, 0.1, 0.1, notifier,,, delayed_CLK, delayed_D);
  endspecify
endmodule
"""


def test_functional_view_drops_specify_and_reads_ports():
    view = functional_view(CELL)
    assert "specify" not in view and "$setuphold" not in view
    assert "delayed_" not in view
    assert "ihp_dff_r (Q, CLK, D, notifier);" in view


def test_functional_view_keeps_cells_without_timing_checks():
    plain = "module inv (Y, A);\n  output Y; input A;\n  not (Y, A);\nendmodule\n"
    assert functional_view(plain) == plain


def test_main_writes_the_view(tmp_path: Path):
    source, output = tmp_path / "cells.v", tmp_path / "out" / "cells_sim.v"
    source.write_text(CELL)
    assert main([str(source), str(output)]) == 0
    assert output.read_text() == functional_view(CELL)


def test_main_rejects_a_view_that_still_has_timing_checks(tmp_path: Path):
    source = tmp_path / "cells.v"
    source.write_text("module m; specify\nendmodule\n")
    with pytest.raises(SystemExit):
        main([str(source), str(tmp_path / "out.v")])
