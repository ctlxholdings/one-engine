"""Tests for one_engine.measures (card 14): streams to numbers, refusing ambiguity."""
import pytest

from one_engine.measures import GUESS, MeasureError, PickedRate, irr, npv, payback


def test_irr_of_a_simple_loan():
    assert irr([-100, 110]) == pytest.approx(0.10, abs=1e-12)


def test_irr_refuses_a_stream_with_no_sign_change():
    with pytest.raises(MeasureError):
        irr([100, 10, 10])


def test_when_several_rates_fit_irr_picks_the_one_nearest_ten_percent_and_flags_the_others():
    # -100 + 230/(1+r) - 132/(1+r)^2 = 0 at r = 10% and r = 20%: Excel's 10% guess lands on 10%
    r = irr([-100, 230, -132])
    assert isinstance(r, PickedRate) and r == pytest.approx(0.10, abs=1e-12)
    assert [round(x, 12) for x in r.roots] == [0.1, 0.2]
    # -100 + 180/(1+r) - 65/(1+r)^2 = 0 at r = -50% and r = 30%: the nearer to 10% is 30%, the second found
    r = irr([-100, 180, -65])
    assert r == pytest.approx(0.30, abs=1e-12) and len(r.roots) == 2
    # the ruler is a stated constant (Excel's default guess), not a number buried in the code
    assert GUESS == 0.10
    # one rate is a plain number: no flag
    assert type(irr([-100, 110])) is float


def test_a_picked_rate_keeps_its_flag_when_a_case_annualises_it():
    from one_engine.cases import _annualised
    monthly = irr([-100, 180, -65])                       # 30% a period, picked from -50% and 30%
    yearly = _annualised(monthly, 12)
    assert isinstance(yearly, PickedRate) and yearly == pytest.approx(1.3 ** 12 - 1)
    assert yearly.roots == pytest.approx((0.5 ** 12 - 1, 1.3 ** 12 - 1))
    assert type(_annualised(irr([-100, 110]), 12)) is float


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
    assert isinstance(xirr([-100, 230, -132], d), PickedRate)                 # several fit: picked, flagged
    with pytest.raises(MeasureError, match="2 dates"):
        xirr([-100, 50, 60], d[:2])
    with pytest.raises(MeasureError, match="before the first"):
        xirr([-100, 50, 60], [d[1], d[0], d[2]])


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
    with pytest.raises(MeasureError, match="before the first"):
        xnpv([1, 2, 3], [d[1], d[0], d[2]], 0.1)


# ── what the library suites need (numpy-financial, pyxirr: one_engine/oracles/library.py) ──

def test_dated_flows_after_the_first_may_come_in_any_order():
    # Excel's XIRR and XNPV, and pyxirr's samples: only a date before the first (the valuation
    # date) is refused; the rest may be listed in any order
    d = [dt.date(2015, 1, 1), dt.date(2016, 1, 1), dt.date(2017, 3, 1)]
    assert xirr([-100, 60, 50], [d[0], d[2], d[1]]) == pytest.approx(xirr([-100, 50, 60], d), abs=1e-12)
    assert xnpv([-100, 60, 50], [d[0], d[2], d[1]], 0.1) == pytest.approx(xnpv([-100, 50, 60], d, 0.1), abs=1e-12)


def test_irr_finds_a_rate_near_minus_100_percent_or_above_1000_percent():
    # one sign change, so one rate; the scan used to stop at -99% and +1000%
    assert irr([-1, 1e-4]) == pytest.approx(-0.9999, abs=1e-12)
    assert irr([-3000, 2.4e-7]) == pytest.approx(-1 + 8e-11, abs=1e-15)
    assert irr([-1, 33.9]) == pytest.approx(32.9, abs=1e-9)
    d = [dt.date(2015, 1, 1), dt.date(2016, 1, 1)]
    assert xirr([-100, 1e-3], d) == pytest.approx(-0.99999, abs=1e-12)
    assert xirr([-1, 33.9], d) == pytest.approx(32.9, abs=1e-9)


def test_irr_scan_survives_long_streams_at_extreme_rates():
    # 400 years: scanning down to a rate near -100%, a discount factor of (1 + r) ** 400 under- or
    # overflows a float unless the scan rescales it, and Python's ** raises rather than returning inf
    stream = [-1000.0] + [1.0] * 399 + [5000.0]
    dates = [dt.date(2000 + t, 1, 1) for t in range(len(stream))]
    for r in (irr(stream), xirr(stream, dates)):
        assert 0 < r < 0.01
    assert abs(npv(stream, irr(stream))) < 1e-6


def test_npv_refuses_a_rate_of_minus_100_percent_or_below():
    # numpy-financial: npv at -1 is NaN (no answer); the engine said ZeroDivisionError
    for rate in (-1, -1.0, -2.5):
        with pytest.raises(MeasureError, match="-100%"):
            npv([0, 1, 2, 3, 4], rate)
