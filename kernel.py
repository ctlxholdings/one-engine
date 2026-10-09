"""The ledger kernel (module card 4).

What it is
----------
The kernel records value and matter. Every other module produces *events*
(a sale, an interest charge, a depreciation tranche, a distribution) and
expresses each as one or more :class:`Posting` objects. The kernel checks
them, holds them, and folds them into balances. It never computes an
economic amount itself, and no module may write a balance directly.

The model is a system of ledgers, not a system of formulas: a balance is
only ever the opening state plus the postings that touched it.

Invariants owned here (consolidation map §8)
-------------------------------------------
* **Two ends.** A posting has a debit account and a different credit
  account, both in the same unit. A one-ended posting cannot be built. For
  money this is double entry; for matter (tonnes, heads, MWh) the same rule
  is conservation: matter moves between stocks or to and from a declared
  :attr:`Nature.BOUNDARY` account, never appears or disappears.
* **Exact identities.** Amounts are :class:`fractions.Fraction`. Balances
  are exact sums, so roll-forward and the per-unit balance identity hold
  with zero tolerance and do not depend on posting order. Step 3 chose this
  after float totals changed with posting order on the nightmare fixture
  (``consolidation/experiments/arith.py``).
* **Direction follows nature.** Each posting declares, for both ends,
  whether it raises (+1) or lowers (-1) that account in its natural sense.
  The kernel computes the same from the account's nature and refuses a
  disagreement. Balancing is not enough: golds had an overdraft whose
  capitalised interest shrank the debt while every payment still matched
  the external ACTUS oracle.
* **Stock bounds.** An account may have a floor (cash >= 0 unless a
  facility funds it; a herd >= 0 head). Bounds are tested when a phase
  closes, not posting by posting, so the order of postings inside a phase
  cannot change the verdict.
* **The clock moves forward only.** A book runs period by period through
  declared phases. Postings go to the current period, at the current phase
  or a later one. Closing a period checks the identity first; a closed
  period cannot be posted to.
* **State travels.** Each posting carries the :class:`~one_engine.inputs.State`
  of the amount it moves. :meth:`Book.states` reports which states reached
  an account in a period, so a stand-in can be traced to every balance it
  touched.

Sign convention
---------------
Balances are stored *naturally*: positive means "more of what this account
is" (more cash, more debt, more revenue, more heads). Debit-natured
accounts (assets, expenses, matter stocks) rise on the debit side;
credit-natured accounts (liabilities, equity, income, boundaries) rise on
the credit side. The per-unit identity is::

    sum(natural balances of debit-natured accounts)
  - sum(natural balances of credit-natured accounts) == 0

which is A = L + E + (income - expenses) for money, and stocks = net
boundary flows for matter.

Not here (other cards)
----------------------
Entity graphs and intercompany pairing (card 5), conversions between units
(Measurement, card 10), classification schemes and named totals (Cuts,
card 13), integrity verdicts over published tables (Checks, card 17).
"""
from __future__ import annotations

import enum
from collections import defaultdict
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from one_engine.inputs import State
from one_engine.units import Number, exact


class KernelError(ValueError):
    """A posting or a book state breaks a kernel invariant."""


class Nature(enum.Enum):
    """What an account is. Decides its natural side.

    STOCK is a physical stock (tonnes, heads, MWh in storage); BOUNDARY is
    where matter enters or leaves the modelled system (births, harvest,
    purchases from outside, sales, losses). Declaring boundaries is what
    makes "matter is neither created nor destroyed" checkable.
    """

    ASSET = "asset"
    EXPENSE = "expense"
    STOCK = "stock"
    LIABILITY = "liability"
    EQUITY = "equity"
    INCOME = "income"
    BOUNDARY = "boundary"

    @property
    def debit_natured(self) -> bool:
        return self in (Nature.ASSET, Nature.EXPENSE, Nature.STOCK)


@dataclass(frozen=True)
class Account:
    """One account in one entity's book.

    ``floor`` is a lower bound on the natural balance, tested at each phase
    close; ``None`` means unbounded (a liability, or an overdraft a facility
    is allowed to fund).
    """

    name: str
    nature: Nature
    unit: str
    floor: Optional[Number] = None

    def __post_init__(self):
        if not self.name or not isinstance(self.name, str):
            raise KernelError(f"an account needs a name, got {self.name!r}")
        if not isinstance(self.nature, Nature):
            raise KernelError(f"account '{self.name}': nature must be a Nature, got {self.nature!r}")
        if not self.unit:
            raise KernelError(f"account '{self.name}' needs a unit")
        if self.floor is not None:
            object.__setattr__(self, "floor", exact(self.floor))


@dataclass(frozen=True)
class Posting:
    """An exact amount moving from the credit account to the debit account.

    Parameters
    ----------
    period, phase:
        When it happens on the book's clock.
    debit, credit:
        Account names; must differ. The direction of the movement is carried
        by which account is which, so ``amount`` is always positive.
    amount:
        Anything :func:`one_engine.units.exact` accepts; stored exactly.
    effect:
        ``{debit: ±1, credit: ±1}``: the module's declared intent, +1 if the
        posting raises that account in its natural sense, -1 if it lowers
        it. Checked against the accounts' natures when posted.
    source, event:
        The producing module and the event or contract that caused it, so
        every movement can be traced.
    state:
        The state of the amount. Defaults to DERIVED (computed by a module);
        an amount taken straight from an input passes its input's state.
    """

    period: int
    phase: str
    debit: str
    credit: str
    amount: Fraction
    effect: Mapping[str, int]
    source: str
    event: str
    state: State = State.DERIVED

    def __post_init__(self):
        if not self.debit or not self.credit:
            raise KernelError(f"posting '{self.event}' needs two ends, got debit={self.debit!r} credit={self.credit!r}")
        if self.debit == self.credit:
            raise KernelError(f"posting '{self.event}' has the same account on both ends: {self.debit}")
        amount = exact(self.amount)
        if amount <= 0:
            raise KernelError(f"posting '{self.event}': amount must be positive (direction is set by the ends), got {amount}")
        object.__setattr__(self, "amount", amount)
        if set(self.effect) != {self.debit, self.credit} or any(v not in (1, -1) for v in self.effect.values()):
            raise KernelError(f"posting '{self.event}': effect must give +1 or -1 for exactly {self.debit} and {self.credit}")
        object.__setattr__(self, "effect", dict(self.effect))
        if not self.source or not self.event:
            raise KernelError("a posting must name its source module and its event")
        if not isinstance(self.state, State):
            raise KernelError(f"posting '{self.event}': state must be a State, got {self.state!r}")


@dataclass(frozen=True)
class Row:
    """One account in one closed period: the published roll-forward line."""

    account: str
    period: int
    opening: Fraction
    movement: Fraction
    closing: Fraction


class Book:
    """One entity's book over a period grid.

    Parameters
    ----------
    entity:
        The entity's name (owned by card 5, Entities).
    periods:
        Number of periods in the grid, indexed 0..periods-1.
    phases:
        The declared order of phases inside each period (for example
        ``("operate", "tax", "finance", "distribute")``). Modules post at
        their phase; bounds are tested as each phase closes.
    accounts:
        Every account the book may touch. Posting to any other name refuses,
        so a typo cannot open a new account that balances and means nothing.
    opening:
        Natural balances at the start of ``start_period``. They must satisfy
        the per-unit identity and every floor, or the book refuses to open.
    start_period:
        The first period this book records. Starting mid-grid is how a model
        runs from actuals or from a purchase date.
    """

    def __init__(self, entity: str, periods: int, phases: Sequence[str],
                 accounts: Iterable[Account], opening: Mapping[str, Number] | None = None,
                 start_period: int = 0):
        if periods < 1:
            raise KernelError("a book needs at least one period")
        if not phases or len(set(phases)) != len(phases):
            raise KernelError(f"phases must be a non-empty list of distinct names, got {phases!r}")
        if not 0 <= start_period < periods:
            raise KernelError(f"start_period {start_period} is outside 0..{periods - 1}")
        self.entity = entity
        self.periods = periods
        self.phases = tuple(phases)
        self.start_period = start_period
        self.accounts: Dict[str, Account] = {}
        for a in accounts:
            if a.name in self.accounts:
                raise KernelError(f"account '{a.name}' is declared twice")
            self.accounts[a.name] = a

        self._opening: Dict[str, Fraction] = defaultdict(Fraction)
        for name, value in (opening or {}).items():
            self._account(name)
            self._opening[name] = exact(value)
        bad = {u: v for u, v in self._identity(self._opening).items() if v != 0}
        if bad:
            raise KernelError(f"opening state does not balance: {bad}")

        self._moves: Dict[Tuple[str, int], Fraction] = defaultdict(Fraction)
        self._closing: Dict[Tuple[str, int], Fraction] = {}   # filled as periods close
        self._states: Dict[Tuple[str, int], Set[State]] = defaultdict(set)
        self._postings: List[Posting] = []
        self._period = start_period          # current period on the clock
        self._phase = 0                      # index of the current phase
        self._closed_through = start_period - 1
        self._check_floors(start_period, label="opening")

    # ── posting ─────────────────────────────────────────────────────────────

    def post(self, p: Posting) -> None:
        """Validate ``p`` and record it.

        Refuses when an account is unknown, the two ends are in different
        units, the declared direction disagrees with the accounts' natures,
        or the clock has already passed the posting's period or phase.
        Posting at a later phase closes the earlier phases first, so their
        bounds are tested before the clock moves.
        """
        debit, credit = self._account(p.debit), self._account(p.credit)
        if debit.unit != credit.unit:
            raise KernelError(f"posting '{p.event}' joins {debit.unit} ({debit.name}) to {credit.unit} ({credit.name})")
        for acct, side in ((debit, +1), (credit, -1)):
            natural = side if acct.nature.debit_natured else -side
            if natural != p.effect[acct.name]:
                raise KernelError(
                    f"direction: posting '{p.event}' declares {p.effect[acct.name]:+d} on "
                    f"{acct.name} ({acct.nature.value}) but its side gives {natural:+d}")
        if p.period != self._period or self._closed_through >= p.period:
            raise KernelError(f"posting '{p.event}' is for period {p.period}; the book is at period {self._period}")
        if p.phase not in self.phases:
            raise KernelError(f"posting '{p.event}': unknown phase '{p.phase}'")
        idx = self.phases.index(p.phase)
        if idx < self._phase:
            raise KernelError(f"posting '{p.event}' is for phase '{p.phase}', already closed in period {p.period}")
        while self._phase < idx:
            self._close_current_phase()
        for acct, side in ((debit, +1), (credit, -1)):
            natural = side if acct.nature.debit_natured else -side
            self._moves[(acct.name, p.period)] += natural * p.amount
            self._states[(acct.name, p.period)].add(p.state)
        self._postings.append(p)

    # ── the clock ───────────────────────────────────────────────────────────

    def close_phase(self, period: int, phase: str) -> None:
        """Close every phase up to and including ``phase`` and test bounds."""
        if period != self._period:
            raise KernelError(f"cannot close a phase of period {period}; the book is at period {self._period}")
        idx = self.phases.index(phase)
        if idx < self._phase:
            raise KernelError(f"phase '{phase}' of period {period} is already closed")
        while self._phase <= idx:
            self._close_current_phase()

    def close_period(self, period: int) -> None:
        """Close the remaining phases, check the identity, advance the clock."""
        if period != self._period or self._closed_through >= period:
            raise KernelError(f"cannot close period {period}; the book is at period {self._period}")
        while self._phase < len(self.phases):
            self._close_current_phase()
        bad = {u: v for u, v in self.imbalance(period).items() if v != 0}
        if bad:                                   # unreachable by construction; kept as a guard
            raise KernelError(f"period {period} does not balance: {bad}")
        self._closed_through = period
        for name in self.accounts:
            self._closing[(name, period)] = self.balance(name, period)
        if period + 1 < self.periods:
            self._period, self._phase = period + 1, 0

    def _close_current_phase(self) -> None:
        if self._phase < len(self.phases):
            self._check_floors(self._period, label=f"phase '{self.phases[self._phase]}'")
            self._phase += 1

    # ── reading ─────────────────────────────────────────────────────────────

    def balance(self, account: str, period: int) -> Fraction:
        """Natural balance of ``account`` at the end of ``period`` (so far, if open)."""
        self._account(account)
        if period < self.start_period:
            raise KernelError(f"period {period} is before this book's start ({self.start_period})")
        if (account, period) in self._closing:
            return self._closing[(account, period)]
        return self.opening(account, period) + self._moves.get((account, period), Fraction(0))

    def opening(self, account: str, period: int) -> Fraction:
        """Natural balance of ``account`` at the start of ``period``."""
        self._account(account)
        if period <= self.start_period:
            return self._opening[account]
        if (account, period - 1) in self._closing:
            return self._closing[(account, period - 1)]
        return self.balance(account, period - 1)

    def imbalance(self, period: int) -> Dict[str, Fraction]:
        """Per-unit identity at the end of ``period``; every value must be 0."""
        return self._identity({a: self.balance(a, period) for a in self.accounts})

    def states(self, account: str, period: int) -> Set[State]:
        """The states of the amounts posted to ``account`` in ``period``."""
        self._account(account)
        return set(self._states.get((account, period), set()))

    def table(self) -> List[Row]:
        """The roll-forward for every account and every closed period."""
        rows = []
        for name in self.accounts:
            opening = self._opening[name]
            for t in range(self.start_period, self._closed_through + 1):
                move = self._moves.get((name, t), Fraction(0))
                rows.append(Row(name, t, opening, move, opening + move))
                opening += move
        return rows

    @property
    def postings(self) -> Tuple[Posting, ...]:
        """Every posting recorded, in the order received (read-only)."""
        return tuple(self._postings)

    # ── internals ───────────────────────────────────────────────────────────

    def _account(self, name: str) -> Account:
        try:
            return self.accounts[name]
        except KeyError:
            raise KernelError(f"unknown account '{name}' in book '{self.entity}'") from None

    def _identity(self, balances: Mapping[str, Fraction]) -> Dict[str, Fraction]:
        out: Dict[str, Fraction] = defaultdict(Fraction)
        for name, acct in self.accounts.items():
            sign = 1 if acct.nature.debit_natured else -1
            out[acct.unit] += sign * balances.get(name, Fraction(0))
        return dict(out)

    def _check_floors(self, period: int, label: str) -> None:
        for name, acct in self.accounts.items():
            if acct.floor is None:
                continue
            bal = self.balance(name, period)
            if bal < acct.floor:
                raise KernelError(
                    f"floor: '{name}' is {float(bal):,.6g} {acct.unit} at the close of {label} "
                    f"in period {period}, below its floor of {float(acct.floor):,.6g}")
