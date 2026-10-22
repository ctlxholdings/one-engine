"""Tests for the annuity measures: pv, fv, pmt, ipmt, ppmt, nper, rate and mirr.

The library suites (numpy-financial, pyxirr) check these against their
published answers in oracles/library.py. The tests here pin what those
suites leave open: the identities that tie the functions together, held
exactly when the inputs are exact; the sign convention; and the refusals.
"""
from fractions import Fraction

import pytest

from one_engine.measures import MeasureError, fv, ipmt, mirr, nper, pmt, ppmt, pv, rate

R, N, LOAN = Fraction(1, 100), 12, Fraction(1000)


@pytest.mark.parametrize("when", [0, 1])
def test_the_payment_repays_the_loan_exactly(when):
    # a loan received (+) repaid by level payments (-): nothing is left after the last one
    p = pmt(R, N, LOAN, when=when)
    assert p < 0
    assert fv(R, N, p, LOAN, when=when) == 0
    assert pv(R, N, p, when=when) == LOAN


@pytest.mark.parametrize("when", [0, 1])
def test_each_payment_is_its_interest_plus_its_principal(when):
    p = pmt(R, N, LOAN, when=when)
    for per in range(1, N + 1):
        assert ipmt(R, per, N, LOAN, when=when) + ppmt(R, per, N, LOAN, when=when) == p
    # the principal parts repay the loan, to the unit
    assert sum(ppmt(R, per, N, LOAN, when=when) for per in range(1, N + 1)) == -LOAN


def test_interest_is_on_the_balance_still_owed():
    # paid in arrears: the first period's interest is on the whole loan, the second's on what is left
    p = pmt(R, N, LOAN)
    assert ipmt(R, 1, N, LOAN) == -R * LOAN
    assert ipmt(R, 2, N, LOAN) == -R * (LOAN + R * LOAN + p)
    # paid in advance: the first payment falls before any interest has run
    assert ipmt(R, 1, N, LOAN, when=1) == 0


def test_at_a_zero_rate_the_loan_is_repaid_in_equal_parts():
    assert pmt(0, 10, 1000) == -100
    assert fv(0, 10, -100, 1000) == 0
    assert pv(0, 10, -100) == 1000
    assert ipmt(0, 3, 10, 1000) == 0
    assert ppmt(0, 3, 10, 1000) == -100
    assert nper(0, -100, 1000) == 10


def test_nper_and_rate_invert_pmt():
    p = pmt(0.01, 12, 1000)
    assert nper(0.01, p, 1000) == pytest.approx(12, abs=1e-9)
    assert rate(12, p, 1000) == pytest.approx(0.01, abs=1e-12)
    p = pmt(0.01, 12, 1000, fv=-200, when=1)
    assert rate(12, p, 1000, fv=-200, when=1) == pytest.approx(0.01, abs=1e-12)


def test_payments_that_never_move_the_balance_take_forever():
    assert nper(0, 0, 1000) == float("inf")


def test_refusals():
    with pytest.raises(MeasureError, match="period 0 .* 1 to 12"):
        ipmt(R, 0, N, LOAN)
    with pytest.raises(MeasureError, match="period 13 .* 1 to 12"):
        ppmt(R, 13, N, LOAN)
    with pytest.raises(MeasureError, match="no payments"):
        pmt(R, 0, LOAN)
    with pytest.raises(MeasureError, match="-100%"):
        fv(-1, N, 0, LOAN)
    with pytest.raises(MeasureError, match="when"):
        pmt(R, N, LOAN, when=2)
    # paying out more each period than the loan never repays it
    with pytest.raises(MeasureError, match="never .* interest outruns"):
        nper(0.1, -50, 1000)
    # a payment the same way as the loan takes the balance away from nothing (Excel answers -10)
    with pytest.raises(MeasureError, match="never .* away from the target"):
        nper(0, 100, 1000)
    # payments and loan of the same sign: no rate repays it
    with pytest.raises(MeasureError, match="no rate"):
        rate(12, 400, 10000, 5000)


def test_mirr_finances_the_outflows_and_reinvests_the_inflows():
    # Excel's MIRR help example: 12.61% at a 10% finance rate and 12% reinvestment rate
    flows = [-120000, 39000, 30000, 21000, 37000, 46000]
    assert mirr(flows, 0.10, 0.12) == pytest.approx(0.126094, abs=1e-6)
    # with both rates equal to the IRR it is the IRR
    assert mirr([-100, 110], 0.10, 0.10) == pytest.approx(0.10, abs=1e-12)
    with pytest.raises(MeasureError, match="both"):
        mirr([100, 50, 25], 0.1, 0.1)


def test_interest_late_in_a_long_term_at_a_high_rate_keeps_its_precision():
    # 14.79% over 300 periods: what is owed late in the term is the small difference of two numbers
    # near 1e20 when taken as a future value; the exact answer (fractions) is the referee
    exact = ipmt(Fraction(1479, 10000), 297, 300, Fraction(-27051, 100))
    assert ipmt(0.1479, 297, 300, -270.51) == pytest.approx(float(exact), rel=1e-12)
    assert float(exact) == pytest.approx(16.9656277018672, abs=1e-9)    # pyxirr's published answer
