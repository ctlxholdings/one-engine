"""Tests for one_engine.loops (card 15): declared fixed points, closed across fresh runs."""
import pytest

from one_engine.loops import Loop, LoopError, close


def sizing_run(values):
    """A toy assembly: a loan sized at half of what the run returns, which depends on the loan."""
    debt = values["size"]["debt"]
    return {"capacity": 100 - debt / 10, "used": debt}


SIZE = Loop("size", {"debt": 0}, lambda run: {"debt": run["capacity"] / 2}, tolerance=1e-9, max_iterations=50)


def test_a_loop_closes_to_its_fixed_point_and_reports_its_residual():
    run, rep = close([SIZE], sizing_run)
    # d = (100 - d/10) / 2  ->  d = 100 / 2.1
    assert run["used"] == pytest.approx(100 / 2.1, abs=1e-8)
    assert rep["size"].residual <= 1e-9 and rep["size"].iterations == len(rep["size"].history)
    assert rep["size"].history[0] == 50                       # from the start of 0


def test_a_loop_whose_rule_ignores_the_loop_closes_in_two_runs_exactly():
    fixed = Loop("size", {"debt": 0}, lambda run: {"debt": 42}, tolerance=0, max_iterations=2)
    _, rep = close([fixed], sizing_run)
    assert rep["size"].iterations == 2 and rep["size"].residual == 0


def test_a_loop_capped_at_one_run_reports_its_residual_and_stops():
    one = Loop("size", {"debt": 0}, SIZE.rule, tolerance=1e-9, max_iterations=1)
    with pytest.raises(LoopError, match=r"loop 'size' residual 50 above tolerance 1e-09 after 1 run"):
        close([one], sizing_run)


def test_a_rule_that_returns_other_outputs_refuses():
    bad = Loop("size", {"debt": 0}, lambda run: {"debt": 1, "tenor": 5}, tolerance=0, max_iterations=3)
    with pytest.raises(LoopError, match="declared"):
        close([bad], sizing_run)


def test_each_run_gets_a_copy_so_a_run_cannot_edit_the_loop_state():
    seen = []

    def meddling_run(values):
        seen.append(values["size"]["debt"])
        values["size"]["debt"] = 999                          # a run that writes back must change nothing
        return sizing_run({"size": {"debt": seen[-1]}})

    close([SIZE], meddling_run)
    assert 999 not in seen


def test_a_number_starts_a_series():
    series = Loop("s", {"x": 0}, lambda run: {"x": [1.0, 1.0]}, tolerance=0, max_iterations=3)
    _, rep = close([series], lambda v: None)
    assert rep["s"].history == [1.0, 0.0] and rep["s"].values == {"x": [1.0, 1.0]}


def test_a_loop_must_declare_an_output_and_a_positive_limit():
    with pytest.raises(LoopError, match="no output"):
        Loop("x", {}, lambda r: {}, 0, 1)
    with pytest.raises(LoopError, match="at least 1"):
        Loop("x", {"a": 0}, lambda r: {}, 0, 0)
