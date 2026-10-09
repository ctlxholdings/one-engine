"""Tests for one_engine.checks: the integrity rule set over published tables.

The kernel makes these identities true by construction. The checks earn
their place on what leaves the kernel: a table written to an export, read
back, edited by a view or produced by another engine (plan step 8 runs the
old engine's tables through the same checks). Each test plants one error in
an otherwise clean table and requires exactly that check to fail.
"""
from dataclasses import replace

from one_engine.checks import integrity
from one_engine.kernel import Account, Book, Nature, Posting


def clean_book():
    b = Book(entity="spv", periods=2, phases=("operate",),
             accounts=[Account("cash", Nature.ASSET, "EUR", floor=0),
                       Account("share_capital", Nature.EQUITY, "EUR"),
                       Account("revenue", Nature.INCOME, "EUR")],
             opening={"cash": 100, "share_capital": 100})
    for t in (0, 1):
        b.post(Posting(period=t, phase="operate", debit="cash", credit="revenue", amount=10,
                       effect={"cash": 1, "revenue": 1}, source="t", event="sale"))
        b.close_period(t)
    return b


def failing(verdicts):
    return {v.name for v in verdicts if not v.passed}


def test_a_clean_book_passes_every_check():
    b = clean_book()
    assert failing(integrity(b.table(), b.accounts)) == set()


def test_a_closing_that_is_not_opening_plus_movement_fails_roll_forward():
    b = clean_book()
    rows = b.table()
    rows[0] = replace(rows[0], closing=rows[0].closing + 1)
    assert "roll_forward" in failing(integrity(rows, b.accounts))


def test_an_opening_that_is_not_last_closing_fails_carry_forward():
    b = clean_book()
    rows = [replace(r, opening=r.opening + 1, closing=r.closing + 1) if (r.account, r.period) == ("cash", 1) else r
            for r in b.table()]
    assert "carry_forward" in failing(integrity(rows, b.accounts))


def test_a_one_sided_edit_fails_the_balance_identity():
    b = clean_book()
    rows = [replace(r, movement=r.movement + 5, closing=r.closing + 5) if (r.account, r.period) == ("revenue", 1) else r
            for r in b.table()]
    assert "balance" in failing(integrity(rows, b.accounts))


def test_a_balance_below_its_floor_fails_bounds():
    b = clean_book()
    rows = [replace(r, opening=r.opening - 200, closing=r.closing - 200) if r.account == "cash" else r
            for r in b.table()]
    rows = [replace(r, opening=r.opening - 200, closing=r.closing - 200) if r.account == "share_capital" else r
            for r in rows]
    assert "bounds" in failing(integrity(rows, b.accounts))


def test_wind_down_names_what_is_left_standing():
    b = clean_book()
    v = {x.name: x for x in integrity(b.table(), b.accounts, wind_down=["cash"])}
    assert not v["wind_down"].passed and "cash" in v["wind_down"].detail


def test_verdicts_are_reported_one_by_one_never_averaged():
    b = clean_book()
    names = [v.name for v in integrity(b.table(), b.accounts, wind_down=[])]
    assert names == ["roll_forward", "carry_forward", "balance", "bounds", "wind_down"]
