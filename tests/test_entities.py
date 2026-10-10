"""Group (card 5) services for rulebooks: one id per event, due versus paid."""
from fractions import Fraction as F

import pytest

from one_engine.contracts import Contract
from one_engine.entities import Group
from one_engine.inputs import State, Value
from one_engine.kernel import KernelError, Nature, Posting
from one_engine.packs.contracts import SHAREHOLDER_LOAN, SPONSOR_EQUITY, TERM_LOAN


def _loan(ctype, cid="shl"):
    terms = dict(commitment=100, rate=F(1, 10), rate_from=1, days_per_period=360, day_count_basis=360,
                 available_until=0, commitment_fee_rate=0, repay_from=5, repay_periods=2)
    return Contract(cid, ctype, {"borrower": "spv"}, {k: Value(v, State.STATED, "t") for k, v in terms.items()}, "USD")


def _group(ctype, amount):
    """Draw 100 in period 0, accrue 10% interest in period 1, pay ``amount`` of it."""
    g = Group(periods=2, phases=["fund", "accrue", "pay", "close"])
    loan = _loan(ctype)
    eq = Contract("eq", SPONSOR_EQUITY, {"issuer": "spv"}, {}, "USD")
    g.add_contract(loan)
    g.add_contract(eq)

    def fund(g, t):
        if t == 0:
            g.settle(g.claims(t)["shl.draw"], 100, "fund")
            g.post_leg(eq, "contribute", 50, t, "fund")

    def pay(g, t):
        if t == 1:
            g.settle(g.claims(t)["shl.interest"], amount, "pay")

    g.register("fund", fund)
    g.register("pay", pay)
    g.run()
    return g


def test_unpaid_interest_is_capitalised_for_a_shareholder_loan():
    b = _group(SHAREHOLDER_LOAN, amount=4).book("spv")
    assert b.balance("shl.debt", 1) == 100 and b.balance("shl.capitalised_interest", 1) == 6
    assert b.balance("shl.interest_payable", 1) == 0


def test_unpaid_interest_stays_due_for_a_term_loan():
    b = _group(TERM_LOAN, amount=4).book("spv")
    assert b.balance("shl.debt", 1) == 100 and b.balance("shl.interest_payable", 1) == 6


def test_paying_more_than_is_due_refuses():
    with pytest.raises(KernelError, match="outside"):
        _group(SHAREHOLDER_LOAN, amount=11)


def test_an_event_id_used_twice_refuses():
    g = Group(periods=1, phases=["fund"])
    eq = Contract("eq", SPONSOR_EQUITY, {"issuer": "spv"}, {}, "USD")
    g.add_contract(eq)

    def twice(g, t):
        for _ in range(2):
            g.post("spv", Posting(t, "fund", "cash", "eq.capital", 1, {"cash": 1, "eq.capital": 1}, "test", "same:0"))

    g.register("fund", twice)
    with pytest.raises(KernelError, match="used twice"):
        g.run()


def _withheld(rate_leg):
    g = Group(periods=2, phases=["fund", "accrue", "pay", "close"])
    loan = _loan(TERM_LOAN)
    eq = Contract("eq", SPONSOR_EQUITY, {"issuer": "spv"}, {}, "USD")
    for c in (loan, eq):
        g.add_contract(c)
    g.declare("spv", "wht", Nature.LIABILITY, "USD")
    g.withhold(rate_leg, F(1, 10), "wht")

    def fund(g, t):
        if t == 0:
            g.settle(g.claims(t)["shl.draw"], 100, "fund")
            g.post_leg(eq, "contribute", 50, t, "fund")

    def pay(g, t):
        if t == 1:
            g.settle(g.claims(t)["shl.interest"], 10, "pay")
            g.post_leg(eq, "distribute", 20, t, "pay")

    g.register("fund", fund)
    g.register("pay", pay)
    g.run()
    return g.book("spv")


def test_withholding_splits_a_payment_between_the_payee_and_the_state():
    b = _withheld("shl.interest")
    assert b.balance("shl.interest_payable", 1) == 0                  # the claim is settled gross
    assert b.balance("wht", 1) == 1                                    # 10% of 10 kept for the state
    assert b.balance("cash", 1) == 150 - 9 - 20                        # the lender received 9
    assert b.balance("wht", 1) == 1 and _withheld("eq.distribute").balance("wht", 1) == 2


def test_withholding_only_applies_to_a_payment_out():
    with pytest.raises(KernelError, match="payment out"):
        _withheld("shl.draw")
    with pytest.raises(KernelError, match="no claim or leg"):
        _withheld("shl.coupon")


# ── opening balances (Macabacus LBO, step 7f) ─────────────────────────────

def _opened(openings, floors=None):
    g = Group(periods=1, phases=["run"], floors=floors)
    for name, nature, value in openings:
        g.open("co", name, nature, "USD", value)
    g.run()
    return g


def test_a_group_opens_on_a_stated_balance_sheet():
    g = _opened([("cash", Nature.ASSET, 100), ("debt", Nature.LIABILITY, 60), ("equity", Nature.EQUITY, 40)])
    assert g.book("co").opening("cash", 0) == 100 and g.book("co").balance("equity", 0) == 40


def test_an_opening_that_does_not_balance_refuses():
    with pytest.raises(KernelError, match="does not balance"):
        _opened([("cash", Nature.ASSET, 100), ("debt", Nature.LIABILITY, 60), ("equity", Nature.EQUITY, F(40) + F(1, 10**14))])


def test_an_opening_whose_nature_disagrees_with_another_declaration_refuses():
    g = Group(periods=1, phases=["run"])
    g.declare("co", "cash", Nature.ASSET, "USD")
    with pytest.raises(KernelError, match="declared as"):
        g.open("co", "cash", Nature.LIABILITY, "USD", 0)


def test_an_account_opened_twice_refuses():
    g = Group(periods=1, phases=["run"])
    g.open("co", "cash", Nature.ASSET, "USD", 0)
    with pytest.raises(KernelError, match="two opening balances"):
        g.open("co", "cash", Nature.ASSET, "USD", 0)
