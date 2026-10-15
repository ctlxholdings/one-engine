"""Techno-economic pack: the small-business operating ladder of the golds library.

What this pack is for
---------------------
The golds library (``cases/reference/golds``) holds hand-derived oracles of
small businesses: a bakery, a moto-taxi, a grain store. Each states its
inputs by name and derives, period by period, a ladder from physical
drivers (loaves a day, trips a year) through revenue, EBITDA, depreciation,
tax and working capital to free cash flow to the firm. A gold is translated
into a one_engine case whose contracts and rulebooks post that business on
the books; the ladder the gold derives is then read back from the books
(:mod:`one_engine.oracles.golds`).

The existing packs carry most of it: ``offtake`` (volume x price, collected
on payment days), ``operating_cost`` (a fixed amount plus a rate per unit,
paid on payment days), ``capex``, ``asset_register`` and ``income_tax``.
This pack adds only what the golds need and those packs do not have:

* the generator ``formulas`` (named values computed from the gold's inputs,
  so a translation never retypes a number) began here and moved to
  :mod:`one_engine.formulas` on 2026-10-15, because ten archetypes use it;
* the rulebooks ``held_balance`` (a working-capital account held at a
  target each period), ``settle`` (payables paid in full in the period they
  arise) and ``transfer`` (a stated amount posted between accounts each
  period) began here and moved to :mod:`one_engine.rulebooks.postings` on
  2026-10-14, because three or more archetypes declare each;
* the rulebook ``write_down``: a stated amount of an asset written off to
  an expense each period (grain lost in store);
* the rulebook ``asset_pool``: a pool of like assets in steady state,
  depreciated at its gross cost over its life each period, the vintage that
  retires replaced by the period's spend.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, List, Mapping

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.formulas import Value
from one_engine.units import exact


#: day 0 of Excel's 1900 date system, as Excel counts from 1 March 1900 on (it counts a 29 February
#: 1900 that never was, so its earlier serials are a day off; no model here dates anything that early)


def _at(x: Value, t: int) -> Fraction:
    if isinstance(x, (list, tuple)):
        return exact(x[t])
    return exact(x)


def write_down(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A stated amount of an asset written off to an expense each period.

    Case keys: ``entity``, ``phase``, ``account`` (the asset, declared by
    another rulebook or the opening balance sheet), ``expense`` (declared
    here), ``amount`` (a number or a series).

    Run before a ``held_balance`` on the same account (an earlier phase),
    the held balance then moves only what the write-down did not: a store
    that loses stock and sells stock reports the loss and the cost of sales
    on separate lines.
    """
    entity, phase, acct, expense = r.take("entity"), r.take("phase"), r.take("account"), r.take("expense")
    amount = r.take("amount")
    group.declare(entity, expense, Nature.EXPENSE, ctx["currency"])

    def handler(g: Group, t: int) -> None:
        x = _at(amount, t)
        if x:
            g.post(entity, Posting(t, phase, expense, acct, x, {expense: 1, acct: -1}, "measurement",
                                   f"{acct}.write_down:{t}"))

    group.register(phase, handler)


def asset_pool(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A steady-state pool of like assets; see the module docstring.

    Case keys: ``entity``, ``phase``, ``from`` (the account the period's
    spend arrives in, moved into service at once), ``to`` (the pool's net
    book value, carried in on the opening balance sheet), ``expense``,
    ``gross`` (the pool's gross cost), ``life_periods`` (the mean life, not
    necessarily whole: a fleet losing 8% a year lives 12.5 years on average).

    Each period is charged ``gross / life_periods``. In a steady state the
    vintage that reaches the end of its life retires at nil book value and
    the period's spend replaces it, so the gross cost, and with it the
    charge, stay level. The rulebook checks the claim it rests on: a
    period whose spend differs from the charge would change the gross
    cost, and is reported as a failed check.
    """
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    src, fa, dep = r.take("from"), r.take("to"), r.take("expense")
    gross, life = exact(r.take("gross")), exact(r.take("life_periods"))
    if life <= 0:
        raise InputError(f"asset_pool: life_periods must be above nil, not {life}")
    for name, nature in ((src, Nature.ASSET), (fa, Nature.ASSET), (dep, Nature.EXPENSE)):
        group.declare(entity, name, nature, unit)
    charge = gross / life
    unsteady: List[str] = []

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        spend = book.balance(src, t)
        if spend:
            g.post(entity, Posting(t, phase, fa, src, spend, {fa: 1, src: -1}, "measurement", f"{fa}.in_service:{t}"))
        if spend != charge:
            unsteady.append(f"period {t}: spend {float(spend):,.2f} is not the charge {float(charge):,.2f}")
        g.post(entity, Posting(t, phase, dep, fa, charge, {dep: 1, fa: -1}, "measurement", f"{fa}.depreciation:{t}"))
        g.record("asset_pool", t, gross=gross, charge=charge, spend=spend)

    group.register(phase, handler)
    return {"steady_state": lambda: unsteady}


RULEBOOKS = {"write_down": write_down, "asset_pool": asset_pool}
