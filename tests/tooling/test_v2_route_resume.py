"""Resume performs bounded existing-route repair and checks the complete layout."""

from tools.v2_route_resume import repair_script


def test_resume_never_uses_full_ripup_and_checks_whole_layout():
    for batch in range(8):
        script = repair_script("/previous/snapshot.odb", "/closure/step", batch)
        steps = [
            line.split()[1:]
            for line in script.splitlines()
            if line.startswith("drt::step_dr ")
        ]
        assert len(steps) == 4
        assert [int(args[7]) for args in steps] == [3, 0, 0, 0]
        assert "set_routing_layers -signal Metal2-Metal4 -clock Metal2-Metal4" in script
        assert "drt::step_end\nwrite_db" in script
        assert f"drt_iter{batch}.odb" in script
        assert f"drt-run-{batch}/whole-layout.drc.rpt" in script
        assert "-box" not in script


def test_repair_batches_change_clip_size_without_raising_maze_effort():
    sizes = []
    for batch in range(4):
        lines = repair_script("/source.odb", "/closure/step", batch).splitlines()
        steps = [
            line.split()[1:] for line in lines if line.startswith("drt::step_dr ")
        ][1:]
        sizes.append(int(steps[0][0]))
        assert all(int(args[2]) == 8 for args in steps)
    assert sizes == [9, 11, 13, 7]
