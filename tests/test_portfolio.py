"""Portfolio and grain (module card 12): copies of a template on a deployment schedule.

The card's first test: a 50-unit programme cut to 30 changes outputs
proportionally where the rules are linear, and nowhere else. The
representative-line grain is linear by construction, so the test is exact.
The other tests pin the shift (``align``, ``first``) and the refusals of
the ``cohorts`` generator (an unknown cut, a misspelt override, a template
outside the case directory, a reserved output name).
"""
from fractions import Fraction as F

import pytest

from one_engine.cases import GENERATORS
from one_engine.inputs import InputError, Reader
from one_engine.portfolio import deploy, online

TEMPLATE = [F(-100), F(10), F(20), F(30)]


def gen(spec):
    r = Reader(spec, source="test")
    kind = r.take("kind")
    out = GENERATORS[kind](r)
    r.finish()
    return out


def cohorts(**over):
    spec = {"kind": "cohorts", "template": "averta_spv.yaml", "schedule": [2, 0, 1], "periods": 6,
            "align": 1, "first": 1, "series": {"distributions": {"cut": "equity_flows", "times": -1}}}
    spec.update(over)
    return spec


def test_copies_are_shifted_to_their_start_and_scaled_by_their_count():
    # two copies start at period 0 and one at period 2; template period 0 sits on the start period
    assert deploy(TEMPLATE, [2, 0, 1], 5) == [-200, 20, -60, 70, 20]
    # align 1, first 1 (the live layout): template period 1 sits on the start, period 0 never enters
    assert deploy(TEMPLATE, [2, 0, 1], 5, align=1, first=1) == [20, 40, 70, 20, 30]


def test_a_programme_cut_from_50_to_30_scales_every_value_by_three_fifths():
    fifty = deploy(TEMPLATE, [20, 0, 30], 6)
    thirty = deploy(TEMPLATE, [12, 0, 18], 6)
    assert thirty == [x * F(3, 5) for x in fifty]
    # and the count by period scales the same way
    assert online([12, 0, 18], 6) == [x * 3 // 5 for x in online([20, 0, 30], 6)]


def test_online_counts_the_copies_started_so_far():
    assert online([30, 30, 60], 5) == [30, 60, 120, 120, 120]
    assert online([30, 30, 60], 2) == [30, 60]


@pytest.mark.parametrize("count", [-1, F(1, 2)])
def test_a_count_must_be_a_non_negative_whole_number(count):
    with pytest.raises(InputError, match="non-negative whole number"):
        deploy(TEMPLATE, [1, count], 3)


def test_the_generator_aggregates_the_template_cut():
    out = gen(cohorts())
    from one_engine.cases import run_case, CASES
    flows = run_case(CASES / "averta_spv.yaml").cut("equity_flows").values
    assert out["distributions"] == deploy([-x for x in flows], [2, 0, 1], 6, align=1, first=1)
    assert out["online"] == [2, 2, 3, 3, 3, 3]
    assert out["deployed"] == [2, 0, 1, 0, 0, 0] and out["count"] == 3


def test_the_generator_refuses_a_cut_the_template_does_not_make():
    with pytest.raises(InputError, match="does not make"):
        gen(cohorts(series={"x": {"cut": "no_such_cut"}}))


def test_an_override_must_name_a_path_the_template_has():
    with pytest.raises(InputError, match="does not have"):
        gen(cohorts(set={"generators.revenu.specific_yield": 1443.0}))


def test_a_template_must_be_a_case_file_in_the_case_directory():
    with pytest.raises(InputError, match="is not a case file"):
        gen(cohorts(template="../kernel.py"))


def test_the_generators_own_output_names_cannot_be_series():
    with pytest.raises(InputError, match="own outputs"):
        gen(cohorts(series={"online": {"cut": "equity_flows"}}))


def test_a_passed_on_output_must_be_one_the_template_generates():
    with pytest.raises(InputError, match="does not generate"):
        gen(cohorts(generated={"zones": "sumproduct.no_such_output"}))
