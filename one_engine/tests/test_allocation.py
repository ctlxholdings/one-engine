"""Allocation (card 7): one operator, conservation, named residual, blocking."""
from fractions import Fraction as F

import pytest

from one_engine.allocation import AllocationError, Tier, allocate


def test_tiers_paid_in_order_and_residual_named():
    a = allocate(100, [Tier("tax", 30), Tier("interest", 50), Tier("principal", 40)], residual="distribution")
    assert a.paid == {"tax": 30, "interest": 50, "principal": 20}
    assert a.residual == 0 and a.residual_name == "distribution"
    a = allocate(100, [Tier("tax", 30)], residual="distribution")
    assert a.residual == 70


def test_conservation_is_exact():
    a = allocate(F(1, 3), [Tier("x", F(1, 7), shares={"a": F(1, 3), "b": F(2, 3)}), Tier("y", 1)], residual="r")
    assert sum(a.paid.values()) + a.residual == F(1, 3)
    assert a.split[("x", "a")] + a.split[("x", "b")] == a.paid["x"]


def test_cap_read_from_state_of_earlier_tiers():
    # a catch-up style cap: pays the gp until it holds 20% of what was paid so far
    def catchup(paid):
        return paid["pref"] / 4
    a = allocate(200, [Tier("pref", 80), Tier("catchup", catchup)], residual="split")
    assert a.paid["catchup"] == 20 and a.residual == 100


def test_blocked_tier_pays_nothing_and_says_why():
    a = allocate(100, [Tier("senior", 60), Tier("shl", 30, blocked_by="dscr_lockup")], residual="distribution")
    assert a.paid["shl"] == 0 and a.blocked == {"shl": "dscr_lockup"}
    assert a.residual == 40


def test_refusals():
    with pytest.raises(AllocationError):
        allocate(-1, [], residual="r")
    with pytest.raises(AllocationError):
        allocate(10, [Tier("x", -5)], residual="r")                         # negative cap
    with pytest.raises(AllocationError):
        allocate(10, [Tier("x", 5, shares={"a": F(1, 2)})], residual="r")   # shares do not sum to 1
    with pytest.raises(AllocationError):
        allocate(10, [Tier("x", 5), Tier("x", 5)], residual="r")            # a tier named twice
    with pytest.raises(AllocationError):
        allocate(10, [Tier("x", 5)], residual="")                           # unnamed residual
