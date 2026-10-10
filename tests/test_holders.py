"""Tests for one_engine.holders (card 11): fully diluted ownership and holders' streams to an exit."""
from fractions import Fraction as F

import pytest

from one_engine.holders import HolderError, dilute, holder_stream


def test_undiluted_ownership_is_pro_rata_to_what_was_paid():
    assert dilute({"sponsor": 75, "management": 25}, []) == {"sponsor": F(3, 4), "management": F(1, 4)}


def test_each_layer_scales_earlier_holdings_down_and_the_shares_sum_to_one():
    own = dilute({"sponsor": 90, "management": 10}, [{"note": F(2, 100)}, {"management": F(5, 1000)}])
    assert own["note"] == F(2, 100) * F(995, 1000)                     # the pool dilutes the kicker too
    assert own["sponsor"] == F(9, 10) * F(98, 100) * F(995, 1000)
    assert own["management"] == F(1, 10) * F(98, 100) * F(995, 1000) + F(5, 1000)
    assert sum(own.values()) == 1


def test_the_order_of_layers_matters():
    a = dilute({"s": 1}, [{"k": F(1, 10)}, {"p": F(1, 10)}])
    b = dilute({"s": 1}, [{"p": F(1, 10)}, {"k": F(1, 10)}])
    assert a["k"] == F(9, 100) and b["k"] == F(1, 10)


@pytest.mark.parametrize("paid,layers,msg", [
    ({"s": -1, "m": 2}, [], "must not be negative"),
    ({"s": 0}, [], "nothing was paid"),
    ({"s": 1}, [{"k": F(-1, 10)}], "negative share"),
    ({"s": 1}, [{"k": F(1, 2), "p": F(1, 2)}], "whole company"),
])
def test_impossible_ownership_refuses(paid, layers, msg):
    with pytest.raises(HolderError, match=msg):
        dilute(paid, layers)


def test_a_holder_stream_is_the_company_flows_reversed_then_the_exit():
    # a note: drawn 50, 5 of interest a year, repaid and 2% of equity value 1,000 at the exit (period 2)
    company = [50, -5, -5, -5]
    assert holder_stream(company, 2, claim=50, share=F(2, 100), equity_value=1000) == [-50, 5, 75, 0]


def test_an_exit_outside_the_stream_refuses():
    with pytest.raises(HolderError, match="outside"):
        holder_stream([1, 2], 2, 0, 0, 0)
