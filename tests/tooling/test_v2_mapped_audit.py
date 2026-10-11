import pytest

from tools.v2_mapped_audit import audit_statistics


def test_unmapped_count_comes_from_final_cell_inventory():
    stats = {
        "modules": {
            "\\top": {
                "num_processes": 0,
                "num_cells_by_type": {"sg13cmos5l_buf_1": 2, "$dff": 3},
            }
        }
    }
    result = audit_statistics(stats, "cell (sg13cmos5l_buf_1) { }", "top")
    assert not result["passed"] and result["unmapped_count"] == 3
    del stats["modules"]["\\top"]["num_cells_by_type"]["$dff"]
    assert audit_statistics(stats, "cell (sg13cmos5l_buf_1) { }", "top")["passed"]
    with pytest.raises(ValueError):
        audit_statistics(stats, "", "top")
    with pytest.raises(ValueError):
        audit_statistics({}, "cell (buf) {}", "top")
