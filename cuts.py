"""Cuts and lenses: named totals read from the books (module card 13).

A cut is a named series built from *classified* lines, never from a
hand-picked list of accounts. The classification is extrinsic: a
:class:`Scheme` maps account names to classes, and the same book can be
read under several schemes (IFRS and SYSCOHADA; two lenders' CFADS
definitions) without changing a posting.

Completeness invariant (map §8; generic-framework's "4th invariant")
------------------------------------------------------------------
Every line a cut could touch must be classified in the scheme. An
unclassified line refuses the cut instead of silently dropping out of it.
That is the failure class behind generic-framework's gum-plant build, where
COGS sat outside a hand-listed tax base and "cash went to -EUR 2.5M".

Views never write: cuts read :attr:`Book.postings` and return new objects.

Cuts of the profit and loss and of balances (added for the Macabacus LBO,
step 7f): :func:`pnl_cut` sums the period movements of the income and
expense accounts in the classes asked for (EBITDA is sales, COGS and SG&A);
:func:`balance_cut` sums closing balances (net debt is debt and preferred
stock less cash). Both read with one sign: credit-natured accounts count
positive, debit-natured negative, so profit and claims come out positive
(income less expense; liabilities less assets). Completeness is by nature:
every income and expense account must be classified for a P&L cut, every
asset, liability and equity account for a balance cut.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, FrozenSet, Iterable, List, Mapping, Set

from one_engine.inputs import State
from one_engine.kernel import Book, Nature


class CutError(ValueError):
    """A cut cannot be built completely."""


@dataclass(frozen=True)
class Scheme:
    """A classification scheme: account name -> class name."""

    name: str
    classes: Mapping[str, str]

    @property
    def class_names(self) -> FrozenSet[str]:
        return frozenset(self.classes.values())


@dataclass(frozen=True)
class Series:
    """A named series of exact values, one per period, with the states that fed it."""

    name: str
    values: List[Fraction]
    states: FrozenSet[State] = frozenset()


def cash_flow_cut(book: Book, cash_account: str, scheme: Scheme, include: Iterable[str],
                  name: str = "") -> Series:
    """Movements of ``cash_account`` whose other end falls in ``include``.

    Each posting that touches the cash account is classified by its other
    end. The direct-method cash flow statement is this cut with every class;
    a project (unlevered) cash flow is the cut without the financing class.

    Raises
    ------
    CutError
        If a counter account is not in the scheme, or ``include`` names a
        class the scheme never uses (a typo would otherwise give zeros).
    """
    include = set(include)
    first = book.start_period
    values = [Fraction(0)] * (book.periods - first)
    states: Set[State] = set()
    missing: Set[str] = set()
    for p in book.postings:
        if cash_account not in (p.debit, p.credit):
            continue
        other = p.credit if p.debit == cash_account else p.debit
        cls = scheme.classes.get(other)
        if cls is None:
            missing.add(other)
            continue
        if cls in include:
            sign = 1 if p.debit == cash_account else -1      # cash is debit-natured
            values[p.period - first] += sign * p.amount
            states.add(p.state)
    if missing:
        raise CutError(f"scheme '{scheme.name}' does not classify: {', '.join(sorted(missing))}")
    unknown = include - scheme.class_names
    if unknown:
        raise CutError(f"scheme '{scheme.name}' has no class {', '.join(sorted(unknown))}")
    return Series(name or f"{book.entity}:{cash_account}:{'+'.join(sorted(include))}", values, frozenset(states))


_PNL = (Nature.INCOME, Nature.EXPENSE)
_BALANCE = (Nature.ASSET, Nature.LIABILITY, Nature.EQUITY)


def _account_cut(book: Book, scheme: Scheme, include: Iterable[str], natures, closing: bool, name: str) -> Series:
    """Sum, per period, the movement (or closing balance) of the accounts of ``natures`` classified in ``include``."""
    include = set(include)
    accounts = [a for a in book.accounts.values() if a.nature in natures]
    unclassified = sorted(a.name for a in accounts if a.name not in scheme.classes)
    if unclassified:
        raise CutError(f"scheme '{scheme.name}' does not classify: {', '.join(unclassified)}")
    no_class = include - scheme.class_names
    if no_class:
        raise CutError(f"scheme '{scheme.name}' has no class {', '.join(sorted(no_class))}")
    first = book.start_period
    values = [Fraction(0)] * (book.periods - first)
    states: Set[State] = set()
    picked = {a.name: (-1 if a.nature.debit_natured else 1) for a in accounts if scheme.classes[a.name] in include}
    for row in book.table(picked):
        if row.account in picked:
            values[row.period - first] += picked[row.account] * (row.closing if closing else row.movement)
    for acct in picked:
        for t in range(first, book.periods):
            states |= book.states(acct, t)
    return Series(name or f"{book.entity}:{'+'.join(sorted(include))}", values, frozenset(states))


def pnl_cut(book: Book, scheme: Scheme, include: Iterable[str], name: str = "") -> Series:
    """Income less expense of the classes in ``include``, period by period (EBITDA, net income).

    Raises
    ------
    CutError
        If an income or expense account is not in the scheme, or ``include``
        names a class the scheme never uses.
    """
    return _account_cut(book, scheme, include, _PNL, closing=False, name=name)


def balance_cut(book: Book, scheme: Scheme, include: Iterable[str], name: str = "") -> Series:
    """Closing balances of the classes in ``include``: liabilities and equity less assets (net debt).

    Raises
    ------
    CutError
        If an asset, liability or equity account is not in the scheme, or
        ``include`` names a class the scheme never uses.
    """
    return _account_cut(book, scheme, include, _BALANCE, closing=True, name=name)
