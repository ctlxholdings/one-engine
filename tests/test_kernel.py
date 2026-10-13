"""Tests for one_engine.kernel: the ledger kernel (card 4).

Every invariant the kernel owns is tested by planting the error it exists
to stop and requiring a refusal. The planted cases come from scars in the
systems being consolidated:

* reversed direction: golds' overdraft whose capitalised interest "SHRANK
  the debt" while every payment still matched ACTUS;
* stock bounds: golds' herd, 12,880 head leaving a 644-head stock;
* determinism: the nightmare fixture's float totals change when the same
  postings are folded in another order (experiments/arith.py).
"""
import random
from fractions import Fraction

import pytest

from one_engine.inputs import State
from one_engine.kernel import Account, Book, KernelError, Nature, Posting


def spv_accounts():
    return [
        Account("cash", Nature.ASSET, "EUR", floor=0),
        Account("debt", Nature.LIABILITY, "EUR"),
        Account("share_capital", Nature.EQUITY, "EUR"),
        Account("revenue", Nature.INCOME, "EUR"),
        Account("interest_expense", Nature.EXPENSE, "EUR"),
    ]


def new_book(**kw):
    args = dict(entity="spv", periods=3, phases=("operate", "finance"),
                accounts=spv_accounts(),
                opening={"cash": 100, "share_capital": 100})
    args.update(kw)
    return Book(**args)


def sale(amount="10", period=0, phase="operate", state=State.STATED):
    return Posting(period=period, phase=phase, debit="cash", credit="revenue",
                   amount=amount, effect={"cash": +1, "revenue": +1},
                   source="contracts", event="ppa", state=state)


# ── two ends ────────────────────────────────────────────────────────────────

def test_a_posting_needs_two_different_ends():
    with pytest.raises(KernelError):
        Posting(period=0, phase="operate", debit="cash", credit="cash", amount=1,
                effect={"cash": +1}, source="t", event="e")
    with pytest.raises(KernelError):
        Posting(period=0, phase="operate", debit="cash", credit=None, amount=1,
                effect={"cash": +1}, source="t", event="e")


def test_amount_is_positive_and_direction_is_carried_by_the_ends():
    for bad in (0, -5):
        with pytest.raises(KernelError):
            sale(amount=bad)


def test_unknown_account_refuses():
    book = new_book()
    with pytest.raises(KernelError):
        book.post(Posting(period=0, phase="operate", debit="cash", credit="revnue",
                          amount=1, effect={"cash": +1, "revnue": +1},
                          source="t", event="typo"))


def test_unit_mismatch_refuses():
    book = new_book(accounts=spv_accounts() + [Account("herd", Nature.STOCK, "head")])
    with pytest.raises(KernelError):
        book.post(Posting(period=0, phase="operate", debit="herd", credit="revenue",
                          amount=1, effect={"herd": +1, "revenue": +1},
                          source="t", event="mixed"))


# ── direction follows nature ────────────────────────────────────────────────

def test_capitalised_interest_that_shrinks_the_debt_refuses():
    """Capitalised interest raises the debt and the expense. The planted
    error swaps the ends: the books still balance, the debt goes down."""
    book = new_book()
    right = Posting(period=0, phase="finance", debit="interest_expense", credit="debt",
                    amount=4, effect={"interest_expense": +1, "debt": +1},
                    source="contracts", event="pik")
    book.post(right)
    swapped = Posting(period=0, phase="finance", debit="debt", credit="interest_expense",
                      amount=4, effect={"interest_expense": +1, "debt": +1},
                      source="contracts", event="pik")
    with pytest.raises(KernelError, match="direction"):
        book.post(swapped)


def test_effect_must_name_both_ends():
    with pytest.raises(KernelError):
        Posting(period=0, phase="operate", debit="cash", credit="revenue", amount=1,
                effect={"cash": +1}, source="t", event="e")


# ── roll-forward and identities ─────────────────────────────────────────────

def test_closing_is_opening_plus_movements_and_rolls_into_next_opening():
    book = new_book()
    book.post(sale("10", 0))
    book.close_period(0)
    book.post(sale("5", 1))
    book.close_period(1)
    rows = {(r.account, r.period): r for r in book.table()}
    assert rows[("cash", 0)].opening == 100 and rows[("cash", 0)].closing == 110
    assert rows[("cash", 1)].opening == 110 and rows[("cash", 1)].closing == 115
    assert rows[("revenue", 1)].movement == 5



def test_the_table_of_some_accounts_is_their_rows_of_the_whole_table():
    book = new_book()
    book.post(sale("10", 0))
    book.close_period(0)
    book.post(sale("5", 1))
    book.close_period(1)
    some = book.table(["revenue"])
    assert some == [r for r in book.table() if r.account == "revenue"] and len(some) == 2
    with pytest.raises(KernelError, match="unknown account"):
        book.table(["no_such_account"])

def test_identity_holds_exactly_not_within_a_tolerance():
    book = new_book()
    for _ in range(10):
        book.post(sale(0.1, 0))      # float 0.1 ten times: exact in the kernel
    book.close_period(0)
    assert book.balance("cash", 0) == 100 + 10 * Fraction(0.1)
    assert book.imbalance(0) == {"EUR": 0}


def test_unbalanced_opening_state_refuses():
    with pytest.raises(KernelError, match="opening"):
        new_book(opening={"cash": 100, "share_capital": 99})


def test_a_book_can_start_from_any_dated_state():
    book = new_book(start_period=1, opening={"cash": 50, "debt": 20, "share_capital": 30})
    with pytest.raises(KernelError):
        book.post(sale("1", 0))                      # before the start: refused
    book.post(sale("1", 1))
    book.close_period(1)
    assert book.balance("cash", 1) == 51


# ── determinism ─────────────────────────────────────────────────────────────

def test_balances_do_not_depend_on_posting_order():
    amounts = [random.Random(s).uniform(0, 1e9) for s in range(500)]
    totals = []
    for seed in (1, 2):
        book = new_book()
        shuffled = amounts[:]
        random.Random(seed).shuffle(shuffled)
        for a in shuffled:
            book.post(sale(a, 0))
        book.close_period(0)
        totals.append(book.balance("cash", 0))
    assert totals[0] == totals[1]


# ── stock bounds and the clock ──────────────────────────────────────────────

def matter_book():
    return Book(entity="ranch", periods=2, phases=("operate",),
                accounts=[Account("herd", Nature.STOCK, "head", floor=0),
                          Account("births", Nature.BOUNDARY, "head"),
                          Account("sales", Nature.BOUNDARY, "head")],
                opening={"herd": 644, "births": 644})


def test_herd_cannot_lose_more_heads_than_it_has():
    book = matter_book()
    book.post(Posting(period=0, phase="operate", debit="sales", credit="herd",
                      amount=12_880, effect={"sales": -1, "herd": -1},
                      source="generator", event="sale"))
    with pytest.raises(KernelError, match="floor"):
        book.close_phase(0, "operate")


def test_a_bound_is_tested_at_phase_close_so_order_inside_a_phase_does_not_matter():
    book = matter_book()
    book.post(Posting(period=0, phase="operate", debit="sales", credit="herd", amount=700,
                      effect={"sales": -1, "herd": -1}, source="g", event="sale"))
    book.post(Posting(period=0, phase="operate", debit="herd", credit="births", amount=100,
                      effect={"herd": +1, "births": +1}, source="g", event="birth"))
    book.close_phase(0, "operate")                # 644 - 700 + 100 = 44 >= 0
    assert book.balance("herd", 0) == 44


def test_cash_floor_is_a_stock_bound_too():
    book = new_book()
    book.post(Posting(period=0, phase="operate", debit="interest_expense", credit="cash",
                      amount=150, effect={"interest_expense": +1, "cash": -1},
                      source="t", event="overpay"))
    with pytest.raises(KernelError, match="floor"):
        book.close_phase(0, "operate")


def test_the_clock_does_not_go_back():
    book = new_book()
    book.post(sale("1", 0, "finance"))
    with pytest.raises(KernelError, match="phase"):
        book.post(sale("1", 0, "operate"))        # earlier phase, same period
    book.close_period(0)
    with pytest.raises(KernelError):
        book.post(sale("1", 0))                   # closed period
    with pytest.raises(KernelError):
        book.post(sale("1", 2))                   # future period


# ── state travels on postings ───────────────────────────────────────────────

def test_a_stand_in_amount_leaves_its_state_on_the_balance():
    book = new_book()
    book.post(sale("10", 0, state=State.STATED))
    book.post(sale("3", 0, state=State.STAND_IN))
    book.close_period(0)
    assert book.states("cash", 0) == {State.STATED, State.STAND_IN}
    assert book.states("debt", 0) == set()
