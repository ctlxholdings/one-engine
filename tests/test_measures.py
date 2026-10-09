"""Tests for one_engine.measures (card 14): streams to numbers, refusing ambiguity."""
import pytest

from one_engine.measures import AmbiguousRate, MeasureError, irr, npv, payback


def test_irr_of_a_simple_loan():
    assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-12)


def test_irr_refuses_a_stream_with_no_sign_change():
    with pytest.raises(MeasureError):
        irr([100, 10, 10])


def test_irr_refuses_when_two_rates_fit():
    # -100 + 230/(1+r) - 132/(1+r)^2 = 0 at r = 10% and r = 20%.
    with pytest.raises(AmbiguousRate) as e:
        irr([-100, 230, -132])
    assert len(e.value.roots) == 2


def test_npv_needs_a_rate():
    with pytest.raises(TypeError):
        npv([-100, 110])            # no default discount rate, ever


def test_simple_payback_interpolates_within_the_period():
    assert payback([-100, 40, 40, 40]) == pytest.approx(2.5)


def test_payback_never_reached_is_reported_not_invented():
    assert payback([-100, 10, 10]) is None
