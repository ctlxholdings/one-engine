"""Integrity checks over published roll-forward tables (module card 17).

What these checks are for
-------------------------
Inside :mod:`one_engine.kernel` the identities hold by construction:
balances are folds of exact two-ended postings. A check that re-tests a
construction "cannot fail and is not this law's content", as golds'
``spec/KERNEL.md`` puts it. These checks therefore run on what *leaves*
the kernel: a list of :class:`~one_engine.kernel.Row` objects that may have
been exported, read back, transformed by a view, or produced by another
engine entirely (plan step 8 runs the old engine's tables through them).

Rules
-----
* Each check returns its own :class:`Verdict`. Verdicts are never averaged
  or merged into a score, so one failure cannot hide behind many passes.
* No check is skipped to make a run green (generic-framework build rule B5;
  its old "SKIP (known S10 double-count)" hid a 2x imbalance).
* Tolerance is zero: tables from this kernel are exact. A table from a float
  engine is compared with an explicit tolerance passed by the caller, with
  its reason in the call.
* Every check is seen failing on a planted error (``tests/test_checks.py``).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Dict, Iterable, List, Mapping, Sequence

from one_engine.kernel import Account, Row
from one_engine.units import Number, exact


@dataclass(frozen=True)
class Verdict:
    """The result of one check: its name, pass or fail, and what failed."""

    name: str
    passed: bool
    detail: str = ""


def integrity(rows: Sequence[Row], accounts: Mapping[str, Account],
              wind_down: Iterable[str] = (), tolerance: Number = 0) -> List[Verdict]:
    """Run the integrity set on a roll-forward table.

    Parameters
    ----------
    rows:
        One row per account per period, as :meth:`Book.table` returns.
    accounts:
        The account declarations (nature, unit, floor) the rows refer to.
    wind_down:
        Accounts that must be zero at the last period (cash, debt, reserves
        in a concession that ends). Empty means the assembly declares no
        terminal wind-down.
    tolerance:
        Absolute tolerance per comparison. Zero for this kernel's tables.

    Returns
    -------
    Five verdicts, always in the same order: roll_forward, carry_forward,
    balance, bounds, wind_down.
    """
    tol = exact(tolerance)
    by_acct: Dict[str, List[Row]] = defaultdict(list)
    for r in rows:
        by_acct[r.account].append(r)
    for rs in by_acct.values():
        rs.sort(key=lambda r: r.period)

    # 1. closing = opening + movement, line by line
    bad = [f"{r.account}@{r.period}" for r in rows if abs(r.opening + r.movement - r.closing) > tol]
    out = [Verdict("roll_forward", not bad, ", ".join(bad[:10]))]

    # 2. next opening = this closing
    bad = [f"{a}@{n.period}" for a, rs in by_acct.items()
           for p, n in zip(rs, rs[1:]) if abs(n.opening - p.closing) > tol]
    out.append(Verdict("carry_forward", not bad, ", ".join(bad[:10])))

    # 3. per unit and period: debit-natured minus credit-natured closings = 0
    sums: Dict[tuple, Fraction] = defaultdict(Fraction)
    for r in rows:
        acct = accounts[r.account]
        sums[(acct.unit, r.period)] += (1 if acct.nature.debit_natured else -1) * r.closing
    bad = [f"{u}@{t}: {float(v):,.6g}" for (u, t), v in sorted(sums.items()) if abs(v) > tol]
    out.append(Verdict("balance", not bad, ", ".join(bad[:10])))

    # 4. floors
    bad = [f"{r.account}@{r.period}" for r in rows
           if accounts[r.account].floor is not None and r.closing < accounts[r.account].floor - tol]
    out.append(Verdict("bounds", not bad, ", ".join(bad[:10])))

    # 5. terminal wind-down
    left = [f"{a}: {float(by_acct[a][-1].closing):,.6g}" for a in wind_down
            if by_acct.get(a) and abs(by_acct[a][-1].closing) > tol]
    out.append(Verdict("wind_down", not left, ", ".join(left)))
    return out
