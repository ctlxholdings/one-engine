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


# ── dated flows and index discounting (Bodmer's solar case, step 7e) ──────

import datetime as dt  # noqa: E402

from one_engine.measures import discount, xirr  # noqa: E402


def test_xirr_counts_actual_days_over_365():
    d0 = dt.date(2015, 1, 1)
    # 365 days later: one year exactly, so the rate is the simple 10%
    assert xirr([-100, 110], [d0, dt.date(2016, 1, 1)]) == pytest.approx(0.10, abs=1e-12)
    # 2016 is a leap year: 366 days is 366/365 of a year, a little under 10%
    assert xirr([-100, 110], [dt.date(2016, 1, 1), dt.date(2017, 1, 1)]) == pytest.approx(
        1.1 ** (365 / 366) - 1, abs=1e-12)


def test_xirr_is_not_moved_by_zero_flows_before_the_first_payment():
    dates = [dt.date(2015, 9, 30), dt.date(2016, 1, 31), dt.date(2017, 1, 31)]
    assert xirr([0, -100, 110], dates) == pytest.approx(xirr([-100, 110], dates[1:]), abs=1e-12)


def test_xirr_refuses_like_irr():
    d = [dt.date(2015, 1, 1), dt.date(2016, 1, 1), dt.date(2017, 1, 1)]
    with pytest.raises(MeasureError, match="never changes sign"):
        xirr([100, 10, 10], d)
    with pytest.raises(AmbiguousRate):
        xirr([-100, 230, -132], d)
    with pytest.raises(MeasureError, match="2 dates"):
        xirr([-100, 50, 60], d[:2])
    with pytest.raises(MeasureError, match="go back"):
        xirr([-100, 50, 60], [d[0], d[2], d[1]])


def test_discount_needs_one_factor_per_flow():
    assert discount([100, 100], [1, 0.5]) == 150
    with pytest.raises(MeasureError, match="discount factors"):
        discount([100, 100, 100], [1, 0.5])


def test_moic_is_money_received_over_money_paid():
    from one_engine.measures import moic
    assert moic([-50, 5.125, 5.125, 5.125, 75.894]) == pytest.approx(91.269 / 50)


def test_moic_refuses_a_stream_with_nothing_invested():
    from one_engine.measures import moic
    with pytest.raises(MeasureError, match="nothing was invested"):
        moic([0, 5, 5])


def test_cash_on_cash_nets_every_later_flow_against_the_investment():
    from one_engine.measures import cash_on_cash, moic
    # the Macabacus long-form preferred: the holder pays 0.125 in twice after investing 9.5
    stream = [-9.5, 0, -0.125, -0.125, 18.34983574987361]
    assert cash_on_cash(stream) == pytest.approx((18.34983574987361 - 0.25) / 9.5)
    # moic counts the later payments as money paid in; cash-on-cash nets them from what comes back
    assert moic(stream) == pytest.approx(18.34983574987361 / 9.75)


def test_cash_on_cash_refuses_a_stream_that_does_not_start_with_an_investment():
    from one_engine.measures import MeasureError, cash_on_cash
    for stream in ([], [0, -5, 6], [5, -5]):
        with pytest.raises(MeasureError, match="does not start with an investment"):
            cash_on_cash(stream)


# ── dated NPV (ModelOff 2014 "Time is Money", step 7p) ─────────────────────

from one_engine.measures import xnpv  # noqa: E402


def test_xnpv_discounts_from_the_first_date_on_actual_days_over_365():
    d = [dt.date(2015, 1, 1), dt.date(2016, 1, 1), dt.date(2017, 1, 1)]
    # the first flow is on the valuation date; 2016 has 366 days
    assert xnpv([-100, 110, 121], d, 0.1) == pytest.approx(-100 + 110 / 1.1 + 121 / 1.1 ** (731 / 365), abs=1e-12)
    with pytest.raises(MeasureError, match="2 dates"):
        xnpv([1, 2, 3], d[:2], 0.1)
    with pytest.raises(MeasureError, match="go back"):
        xnpv([1, 2, 3], [d[0], d[2], d[1]], 0.1)
