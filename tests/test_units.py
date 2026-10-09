"""Tests for one_engine.units: exact quantities with explicit units.

Card 1 (Time and units) in the consolidation's MODULE_CARDS.md: units and
bases are explicit, and nothing converts silently. Each test below plants
the error the invariant exists to stop.
"""
from decimal import Decimal
from fractions import Fraction

import pytest

from one_engine.units import Quantity, UnitError, exact


def test_exact_keeps_every_float_bit():
    # A float is a dyadic rational; exact() must keep it, not round it.
    assert exact(0.1) == Fraction(3602879701896397, 36028797018963968)


def test_exact_reads_decimal_text_as_the_decimal_it_names():
    # "0.1" typed by a person means one tenth, not the float nearest to it.
    assert exact("0.1") == Fraction(1, 10)
    assert exact(Decimal("266161.51")) == Fraction(26616151, 100)


def test_exact_refuses_what_is_not_a_number():
    for bad in (None, "ten", float("nan"), float("inf"), True):
        with pytest.raises(TypeError):
            exact(bad)


def test_adding_two_currencies_refuses():
    # Planted error: EUR + USD with no conversion.
    with pytest.raises(UnitError):
        Quantity(1, "EUR") + Quantity(1, "USD")


def test_same_unit_adds_exactly():
    q = Quantity("0.1", "EUR") + Quantity("0.2", "EUR")
    assert q == Quantity("0.3", "EUR")          # exact, unlike 0.1 + 0.2 in float


def test_comparison_across_units_refuses():
    with pytest.raises(UnitError):
        Quantity(1, "t") < Quantity(1, "head")
