"""Tax: what is owed to the state (module card 9).

Built so far: a corporate income tax on a base read from the classified
books, with a loss pool held as dated vintages, and deferred tax on
temporary differences and unused losses (the nightmare case: tax
depreciation over 10 years against book depreciation over 20).
Withholding is a property of payments and lives with them
(:meth:`one_engine.entities.Group.withhold`).

Completeness of the base
------------------------
The base is never a hand-picked list of deductible lines. A scheme
classifies **every** profit-and-loss account of the book (income and
expense natures) as ``taxable``, ``exempt`` or ``non_deductible``. An
account the scheme does not classify refuses the base. That is the failure
behind generic-framework's gum-plant build, where COGS sat outside a
hand-listed base and "cash went to -EUR 2.5M"
(``docs/engine/COMPLETENESS_INVARIANT.md``).

A fourth class, ``timing``, marks a line whose amount the tax authority
measures differently (book depreciation, replaced by tax depreciation).
It is left out of the base here, and the caller adds the tax measure of
the same item; the scheme is still complete, because the line is
classified rather than forgotten.

Deferred tax
------------
:func:`deferred_tax` is the balance-sheet method (IAS 12): the rate times
the temporary difference between the book carrying amount and the tax base
of the assets, less the rate times the losses still available. Positive is
a liability, negative an asset. Its movement each period is the deferred
tax charge, so current plus deferred tax equals the rate times the
accounting profit (less exempt and non-deductible lines) whenever no loss
expires unused.

Losses carried as base or as tax
--------------------------------
Statutes carry a loss as an amount of the base: it saves tax at the rate
of the year it is used in. ModelOff 2015 "Tax" carries it as tax instead
(the rate of its own month times the loss), so it saves tax at the rate
it arose at. :class:`TaxPolicy` declares which (``losses_in``); with a
constant rate the two are the same.

Losses expire on the grid's own clock
-------------------------------------
A carry-forward is stated in periods of the grid. A five-year limit on a
quarterly grid is 20 periods, so a loss from period 0 is usable through
period 19 and gone at period 20 (card 9 first test). ``None`` means no
expiry. Vintages are used first in, first out.
"""
from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import List, Mapping, Optional

from one_engine.kernel import Book, Nature
from one_engine.units import Number, exact


class TaxError(ValueError):
    """The taxable base cannot be built completely, or a policy is invalid."""


_CLASSES = ("taxable", "exempt", "non_deductible", "timing")


def taxable_base(book: Book, period: int, scheme: Mapping[str, str]) -> Fraction:
    """Income minus expense over the accounts classified ``taxable``, for ``period``.

    Reads the period's movements from the book so far (call it once the
    lines that enter the base are posted).
    """
    pl = [a for a in book.accounts.values() if a.nature in (Nature.INCOME, Nature.EXPENSE)]
    missing = sorted(a.name for a in pl if a.name not in scheme)
    if missing:
        raise TaxError(f"tax scheme does not classify: {', '.join(missing)}")
    bad = sorted(k for k, v in scheme.items() if v not in _CLASSES)
    if bad:
        raise TaxError(f"tax scheme uses unknown classes for: {', '.join(bad)} (allowed {_CLASSES})")
    base = Fraction(0)
    for a in pl:
        if scheme[a.name] != "taxable":
            continue
        move = book.balance(a.name, period) - book.opening(a.name, period)
        base += move if a.nature is Nature.INCOME else -move
    return base


class LossPool:
    """Tax losses as dated vintages, used first in, first out."""

    def __init__(self, carry_forward_periods: Optional[int]):
        if carry_forward_periods is not None and carry_forward_periods < 1:
            raise TaxError("carry_forward_periods must be positive or None")
        self.limit = carry_forward_periods
        self._vintages: List[List] = []          # [period, remaining]

    def add(self, period: int, loss: Number) -> None:
        loss = exact(loss)
        if loss > 0:
            self._vintages.append([period, loss])

    def _alive(self, period: int):
        return [v for v in self._vintages if self.limit is None or period - v[0] < self.limit]

    def available(self, period: int) -> Fraction:
        return sum((v[1] for v in self._alive(period)), Fraction(0))

    def use(self, period: int, amount: Number) -> Fraction:
        """Use up to ``amount`` of live losses; returns the amount used."""
        want = exact(amount)
        used = Fraction(0)
        for v in self._alive(period):
            x = min(v[1], want - used)
            v[1] -= x
            used += x
        return used


_LOSSES_IN = ("base", "tax")


@dataclass(frozen=True)
class TaxPolicy:
    """A flat rate on the base after losses.

    ``losses_in`` says what the loss pool holds. ``"base"`` (the default,
    and the usual statute): losses of the base, used against later base, so
    a loss saves tax at the rate of the period it is used in. ``"tax"``
    (ModelOff 2015 "Tax", step 7k): the period's rate times the loss, used
    against later tax, so a loss saves tax at the rate of the period it
    arose in. The two differ only when the rate changes.
    """

    rate: Fraction
    carry_forward_periods: Optional[int]
    losses_in: str = "base"

    def __post_init__(self):
        if self.losses_in not in _LOSSES_IN:
            raise TaxError(f"losses_in must be one of {_LOSSES_IN}, got {self.losses_in!r}")

    def charge(self, period: int, base: Number, pool: LossPool) -> Fraction:
        """Tax for ``period``. A negative base adds a loss vintage; a positive one uses losses first."""
        base = exact(base)
        amount = exact(self.rate) * base if self.losses_in == "tax" else base
        if amount <= 0:
            pool.add(period, -amount)
            return Fraction(0)
        used = pool.use(period, amount)
        return amount - used if self.losses_in == "tax" else exact(self.rate) * (amount - used)


def deferred_tax(rate: Number, carrying: Number, tax_base: Number, losses: Number) -> Fraction:
    """Net deferred tax balance: ``rate x (carrying - tax_base - losses)``; positive is a liability."""
    for name, v in (("carrying", carrying), ("tax_base", tax_base), ("losses", losses)):
        if exact(v) < 0:
            raise TaxError(f"deferred tax: {name} must not be negative, got {exact(v)}")
    return exact(rate) * (exact(carrying) - exact(tax_base) - exact(losses))
