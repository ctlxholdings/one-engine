"""Tests for one_engine.runs: worlds layered onto a base, and what each party gains or loses in one.

The composer came from the failure panel's test (``test_case_esco_panel.py``
runs the ten worlds through it); these tests hold the parts the credit pilot
added (2026-10-15): the inputs a world moves, and the exposure table, each
party's cut in a world less the same cut in the base.
"""
from fractions import Fraction as F
from types import SimpleNamespace

import pytest

from one_engine.cuts import Series
from one_engine.runs import compose, exposure, moved


def test_a_world_writes_only_where_the_base_has_a_surface():
    base = {"generators": {"src": {"inputs": {"achievement_pct": 100}}}}
    got = compose(base, {"world": "s90", "set": {"generators.src.inputs.achievement_pct": 90}})
    assert got["generators"]["src"]["inputs"]["achievement_pct"] == 90
    assert base["generators"]["src"]["inputs"]["achievement_pct"] == 100       # the base is not touched
    with pytest.raises(ValueError, match="no generators.src.inputs.yield to set"):
        compose(base, {"world": "w", "set": {"generators.src.inputs.yield": 1}})
    with pytest.raises(ValueError, match="binds nothing"):
        compose(base, {"world": "w", "set_where_present": {"contracts.x.terms.rate": 1}})


def test_the_inputs_a_world_moves_are_the_names_it_writes_values_to():
    p90 = {"world": "p90", "add": {
        "generators.portfolio": {"set": {"generators.revenue.specific_yield": 1443}},
        "generators.holdco": {"set": {"generators.portfolio.set": {"generators.revenue.specific_yield": 1443}}}}}
    assert moved(p90) == ["specific_yield"]
    s75 = {"world": "s75", "set": {"generators.src.inputs.achievement_pct": 75},
           "set_where_present": {"generators.src.inputs.esif_pays": 0}}
    assert moved(s75) == ["achievement_pct", "esif_pays"]


def _result(**cuts):
    return SimpleNamespace(cut=lambda name: Series(name, [F(x) for x in cuts[name]]))


def test_exposure_is_each_party_cut_in_the_world_less_the_base():
    base = _result(lender=[0, -10, -10], equity=[-5, 4, 4])
    world = _result(lender=[0, -10, -10], equity=[-5, 1, 2])
    got = exposure(base, world, {"lender": ("lender", -1), "equity": ("equity", 1)})
    assert got == {"lender": [0, 0, 0], "equity": [0, -3, -2]}


def test_a_party_seen_from_the_deal_side_takes_its_sign():
    # the lender's receipts are the borrower's payments: a world that cuts debt service costs the lender
    base, world = _result(debt=[100, -60, -60]), _result(debt=[100, -60, -40])
    assert exposure(base, world, {"lender": ("debt", -1)}) == {"lender": [0, 0, -20]}


def test_an_exposure_on_runs_of_different_lengths_refuses():
    with pytest.raises(ValueError, match="lender.*3 periods.*2"):
        exposure(_result(c=[1, 2, 3]), _result(c=[1, 2]), {"lender": ("c", 1)})


def test_a_party_sign_other_than_one_or_minus_one_refuses():
    with pytest.raises(ValueError, match="sign"):
        exposure(_result(c=[1]), _result(c=[1]), {"lender": ("c", 2)})
