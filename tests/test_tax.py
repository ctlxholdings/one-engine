"""Tax (card 9): complete base, loss vintages that expire on the grid's clock."""
from fractions import Fraction as F

import pytest

from one_engine.kernel import Account, Book, Nature, Posting
from one_engine.tax import LossPool, TaxError, TaxPolicy, taxable_base


def _book():
    accts = [Account("cash", Nature.ASSET, "EUR"), Account("revenue", Nature.INCOME, "EUR"),
             Account("opex", Nature.EXPENSE, "EUR"), Account("fine", Nature.EXPENSE, "EUR")]
    b = Book("spv", 2, ["operate"], accts)
    b.post(Posting(0, "operate", "cash", "revenue", 100, {"cash": 1, "revenue": 1}, "t", "s"))
    b.post(Posting(0, "operate", "opex", "cash", 30, {"opex": 1, "cash": -1}, "t", "o"))
    b.post(Posting(0, "operate", "fine", "cash", 5, {"fine": 1, "cash": -1}, "t", "f"))
    return b


def test_base_from_classified_lines():
    scheme = {"revenue": "taxable", "opex": "taxable", "fine": "non_deductible"}
    assert taxable_base(_book(), 0, scheme) == 70


def test_an_unclassified_profit_and_loss_line_refuses():
    # the COGS-outside-the-base failure: a new expense line nobody classified
    with pytest.raises(TaxError, match="fine"):
        taxable_base(_book(), 0, {"revenue": "taxable", "opex": "taxable"})
    with pytest.raises(TaxError):
        taxable_base(_book(), 0, {"revenue": "taxable", "opex": "taxable", "fine": "deductible?"})


def test_loss_vintage_expires_on_the_clock():
    # five-year carry-forward on a quarterly grid: usable after 19 quarters, gone after 20
    pool = LossPool(carry_forward_periods=20)
    pool.add(period=0, loss=100)
    assert pool.available(19) == 100
    assert pool.available(20) == 0
    pool = LossPool(carry_forward_periods=20)
    pool.add(period=0, loss=100)
    assert pool.use(5, 40) == 40 and pool.available(6) == 60      # FIFO use


def test_policy_charge_uses_losses_first():
    pol = TaxPolicy(rate=F(15, 100), carry_forward_periods=None)
    pool = LossPool(None)
    assert pol.charge(0, F(-50), pool) == 0 and pool.available(1) == 50
    assert pol.charge(1, F(80), pool) == F(15, 100) * 30 and pool.available(2) == 0


def test_timing_lines_stay_classified_and_out_of_the_base():
    scheme = {"revenue": "taxable", "opex": "timing", "fine": "non_deductible"}
    assert taxable_base(_book(), 0, scheme) == 100                     # the caller adds the tax measure


def test_deferred_tax_is_the_rate_on_the_temporary_difference_less_losses():
    from one_engine.tax import deferred_tax
    assert deferred_tax(F(1, 4), carrying=80, tax_base=60, losses=0) == 5        # a liability
    assert deferred_tax(F(1, 4), carrying=80, tax_base=60, losses=40) == -5      # net asset
    with pytest.raises(TaxError):
        deferred_tax(F(1, 4), carrying=-1, tax_base=0, losses=0)
