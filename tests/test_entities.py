"""Group (card 5) services for rulebooks: one id per event, due versus paid."""
from fractions import Fraction as F

import pytest

from one_engine.contracts import Contract
from one_engine.entities import Group
from one_engine.inputs import State, Value
from one_engine.kernel import KernelError, Posting
from one_engine.packs.contracts import SHAREHOLDER_LOAN, SPONSOR_EQUITY, TERM_LOAN


def _loan(ctype, cid="shl"):
    terms = dict(commitment=100, rate=F(1, 10), rate_from=1, days_per_period=360, day_count_basis=360,
                 available_until=0, repay_from=5, repay_amount=50)
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
    assert b.balance("shl.debt", 1) == 106 and b.balance("shl.interest_payable", 1) == 0


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
