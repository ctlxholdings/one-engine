"""Tax rulebook (module card 9): income tax.

The base, the loss pool and deferred tax are :mod:`one_engine.tax`; this
rulebook reads the case's tax terms and posts the charge, credits and the
deferred balance. Moved from :mod:`one_engine.packs.pf` on 2026-10-14 (Ali chose to move the rulebooks three or more
archetypes declare out of the packs; ``one_engine/archetypes.yaml``).

Rulebooks
---------
``income_tax``
    The base comes from a scheme classifying every profit-and-loss account
    of the book (:func:`~one_engine.tax.taxable_base`). The charge goes to
    ``tax_payable``; the waterfall pays it. With ``timing: <register>``,
    the lines classified ``timing`` (book depreciation and disposal) are
    replaced in the base by that register's tax depreciation. With
    ``deferred``, the net deferred tax balance
    (:func:`~one_engine.tax.deferred_tax`, on the registers' NBVs and the
    losses carried forward) is kept in its account, its movement charged
    to a deferred tax expense line. ``deferred.nil_tax_base`` (Macabacus
    LBO, step 7f) lists assets whose cost was deducted for tax when paid
    (capitalised financing fees): their carrying amount is a temporary
    difference with a nil tax base, so the deferred balance moves by the
    rate times their movement. That mode measures movements only, so a
    deferred balance carried in from an opening balance sheet keeps the
    part this rulebook does not track; it cannot be combined with a
    ``timing`` register. ``from`` (default 0) is the first period charged:
    a deal's own tax at the close is the acquisition's (step 7f).
    ``rate`` is a number or one per period (a change of rate, ModelOff 2012
    "Asset Schedule", step 7i): the deferred balance is measured at each
    period's rate, and the tax record's ``rate_change`` is the part of its
    movement due to the change (the new rate less the old, times last
    period's difference), beside the ``carrying`` amount and ``tax_base``
    it was measured on. ``deferred.recognise_losses: false`` measures the
    deferred balance on the assets alone: no deferred tax asset on unused
    losses, as IAS 12.34 requires when no taxable profit is expected
    against them (the case states no income).

    ``bands`` replaces ``rate`` with a progressive schedule (the golds'
    paygo_solar, pharmacy_expiry, printer_cashbasis_v25): a list of
    ``{upto, rate}``, ceilings rising, the last band ``{rate}`` alone; each
    band taxes the taxable amount (after losses) inside it. Bands set on a
    year's income over a grid of shorter periods take ``bands_per_year``:
    each period's taxable amount is banded at its yearly equivalent (times
    that number) and the tax brought back to the period (divided by it),
    which is the year's tax when the periods are level. The record carries
    ``band<i>_base`` and ``band<i>_tax`` per band, from 1. Built for losses
    carried forward as base, without ``deferred``.

    ``minimum`` (the golds' pilots: OHADA's "impôt minimum forfaitaire")
    charges the higher of the profits tax and a minimum on turnover:
    ``rate`` times the period's ``turnover`` (the income lines listed),
    raised to ``floor`` and lowered to ``cap`` (each optional; any of the
    three a number or one per period). ``applies`` (default 1; 0 in a
    period whose holiday covers the minimum too) says where it is charged;
    it is computed every period regardless, because the golds report it.
    The minimum neither feeds nor draws on the loss pool. The tax record
    then carries ``profit_tax`` (the rate on the base after losses),
    ``minimum`` and ``tax``, what is charged.

    ``losses_in: tax`` carries losses as tax, at the rate of the period
    they arose in (``TaxPolicy.losses_in``: ModelOff 2015 "Tax", step 7k);
    the tax record's ``expired`` is what the period could use but the next
    cannot.

    ``loss_relief: refund`` (default ``carry_forward``) relieves a loss in
    the period it arises, at that period's rate: the charge is the rate
    times the base, a negative base giving a tax credit (a refund, group
    relief, or an annual tax accrued period by period, as the golds'
    ``grain`` trader books it month by month). Nothing is carried, so the
    loss pool stays empty; it cannot be combined with ``deferred``.

    The tax record also carries the loss pool's roll-forward (the golds
    library reports it): ``losses_opening``, ``loss_added``, ``loss_used``
    and the ``taxable`` amount left after losses (base mode only), with
    ``losses`` the closing pool. The record is kept under the rulebook's
    name (``tax`` in every case so far), so a book may carry two taxes (the
    golds' ``arise_expiry`` taxes its salvage apart from its operations).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Mapping

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.tax import LossPool, TaxPolicy, deferred_tax, taxable_base
# a term stated once or per period (``_at`` keeps the name the packs use for the same reading)
from one_engine.units import at as _at, exact


def income_tax(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    expense, payable = r.take("expense"), r.take("payable")
    start = int(r.take_or_default("from", 0, reason="tax is charged from the first period").value)
    cf = r.take("carry_forward_periods")
    cf = None if cf is None else int(cf)
    rates = r.take_or_default("rate", None, reason="the tax is charged in bands").value   # or one per period
    bands = r.take_or_default("bands", None, reason="one rate on the whole base").value
    per_year = exact(r.take_or_default("bands_per_year", 1, reason="the bands apply to each period's base").value)
    if (rates is None) == (bands is None):
        raise InputError("income_tax: give a rate or bands, not both and not neither")
    losses_in = r.take_or_default("losses_in", "base", reason="losses are carried as amounts of the base").value
    relief = r.take_or_default("loss_relief", "carry_forward", reason="a loss is carried to later profit").value
    if relief not in ("carry_forward", "refund"):
        raise InputError(f"income_tax: loss_relief is carry_forward or refund, not {relief!r}")
    if bands is not None:
        # every band but the last has a ceiling; the last runs on without one
        bands = [Reader(b, source=f"income_tax.bands[{i}]") for i, b in enumerate(bands)]
        bands = [(b.take("upto") if i < len(bands) - 1 else None, b.take("rate"), b.finish())[:2]
                 for i, b in enumerate(bands)]
        if not bands or losses_in != "base" or relief != "carry_forward":
            raise InputError("income_tax: bands are built for losses carried forward as base, and need a band")
    scheme = dict(r.take("scheme"))
    minimum = r.take_or_default("minimum", None, reason="no minimum tax: the charge is the profits tax").value
    if minimum is not None:
        mr = Reader(minimum, source="income_tax.minimum")
        m_lines, m_rate = list(mr.take("turnover")), mr.take("rate")
        m_floor = mr.take_or_default("floor", 0, reason="no floor: the minimum is the rate on turnover").value
        m_cap = mr.take_or_default("cap", None, reason="no cap on the minimum").value
        m_applies = mr.take_or_default("applies", 1, reason="the minimum is charged in every period").value
        mr.finish()
    group.declare(entity, expense, Nature.EXPENSE, unit)
    group.declare(entity, payable, Nature.LIABILITY, unit)
    pool = LossPool(cf)
    timing_name = r.take_or_default("timing", None, reason="no line is measured differently for tax").value
    deferred = r.take_or_default("deferred", None, reason="no deferred tax recognised").value
    timing_lines = sorted(k for k, v in scheme.items() if v == "timing")
    if (timing_name is None) != (not timing_lines):
        raise InputError("income_tax: lines classified 'timing' need a 'timing' register, and only then")
    nil_base = []
    if deferred is not None:
        dr = Reader(deferred, source="income_tax.deferred")
        dt_acct, dt_expense = dr.take("account"), dr.take("expense")
        nil_base = list(dr.take_or_default("nil_tax_base", [], reason="no asset has a nil tax base").value)
        recognise_losses = dr.take_or_default("recognise_losses", True,
                                              reason="a deferred tax asset is recognised on unused losses").value
        if recognise_losses not in (True, False):
            raise InputError(f"income_tax: deferred.recognise_losses is true or false, got {recognise_losses!r}")
        dr.finish()
        if losses_in != "base":
            raise InputError("income_tax: deferred tax on losses carried as tax (losses_in: tax) is not built")
        if relief == "refund":
            raise InputError("income_tax: deferred tax with losses refunded (loss_relief: refund) is not built")
        if bands is not None:
            raise InputError("income_tax: deferred tax measured on bands is not built")
        if nil_base and timing_name is not None:
            raise InputError("income_tax: deferred.nil_tax_base measures movements and cannot be combined "
                             "with a 'timing' register")
        group.declare(entity, dt_acct, Nature.LIABILITY, unit)
        group.declare(entity, dt_expense, Nature.EXPENSE, unit)

    # the temporary difference (net of recognised losses) and the rate it was measured at, last period:
    # a change of rate remeasures it, and the tax record shows that part of the deferred movement
    last = {"diff": Fraction(0), "rate": None}

    def handler(g: Group, t: int) -> None:
        if t < start:
            return
        # with bands the policy only measures what losses leave taxable (a rate of 1); the bands tax that
        policy = TaxPolicy(Fraction(1) if bands is not None else _at(rates, t), cf, losses_in)
        base = taxable_base(g.book(entity), t, scheme)
        if timing_name is not None:
            regs = ctx["registers"][timing_name]
            if timing_lines != sorted({regs.expense, regs.disposal}):
                raise InputError(f"income_tax: 'timing' lines {timing_lines} are not the lines of register "
                                 f"'{timing_name}' ({regs.expense}, {regs.disposal})")
            base -= regs.tax_moves.get(t, Fraction(0))
        opening = pool.available(t)
        # a refunded loss is relieved now, at the rate: the pool is never fed, so it stays empty
        tax = policy.rate * base if relief == "refund" else policy.charge(t, base, pool)
        banded = {}
        if bands is not None:
            # the period's taxable amount at its yearly equivalent, banded, and brought back to the period
            left, floor_, tax = tax * per_year, Fraction(0), Fraction(0)
            for i, (upto, rate) in enumerate(bands, 1):
                top = None if upto is None else _at(upto, t)
                if top is not None and top < floor_:
                    raise InputError(f"income_tax: bands must rise; band {i} ends at {top}, below {floor_}")
                inside = left if top is None else min(left, top - floor_)
                banded[f"band{i}_base"] = inside / per_year
                banded[f"band{i}_tax"] = _at(rate, t) * inside / per_year
                tax += banded[f"band{i}_tax"]
                left -= inside
                floor_ = top if top is not None else floor_
        profit_tax = tax
        if minimum is not None:
            book = g.book(entity)
            wrong = [a for a in m_lines if book.accounts[a].nature is not Nature.INCOME]
            if wrong:
                raise InputError(f"income_tax: minimum.turnover lists {wrong}, which are not income lines")
            turnover = sum((book.balance(a, t) - book.opening(a, t) for a in m_lines), Fraction(0))
            least = max(_at(m_rate, t) * turnover, _at(m_floor, t))
            least = least if m_cap is None else min(least, _at(m_cap, t))
            if _at(m_applies, t):
                tax = max(tax, least)
        if tax > 0:
            g.post(entity, Posting(t, phase, expense, payable, tax, {expense: 1, payable: 1}, "tax", f"{ctx['name']}.charge:{t}"))
        elif tax < 0:
            # a credit: the expense falls, and the payable with it (below nil it is a refund due)
            g.post(entity, Posting(t, phase, payable, expense, -tax, {payable: -1, expense: -1}, "tax",
                                   f"{ctx['name']}.credit:{t}"))
        # what this period could still use but the next cannot: the vintages that expire at its end
        rec = dict(base=base, tax=tax, losses=pool.available(t + 1), expired=pool.available(t) - pool.available(t + 1))
        if minimum is not None:
            rec.update(profit_tax=profit_tax, minimum=least)
        rec.update(banded)
        # the pool's roll-forward (the golds report it): opening + added - used = what this period can still use
        added = Fraction(0) if relief == "refund" else \
            max(Fraction(0), -base) if losses_in == "base" else max(Fraction(0), -policy.rate * base)
        used = opening + added - pool.available(t)
        rec.update(losses_opening=opening, loss_added=added, loss_used=used,
                   taxable=max(Fraction(0), base - used) if losses_in == "base" else None)
        if deferred is not None:
            book = g.book(entity)
            if nil_base:
                # the carrying amount of an asset deducted when paid is all temporary difference
                move = policy.rate * sum((book.balance(a, t) - book.opening(a, t) for a in nil_base), Fraction(0))
            else:
                regs = ctx["registers"][timing_name] if timing_name else None
                carrying = regs.book.nbv() if regs else Fraction(0)
                tax_base = regs.tax.nbv() if regs else Fraction(0)
                losses = pool.available(t + 1) if recognise_losses else Fraction(0)
                target = deferred_tax(policy.rate, carrying, tax_base, losses)
                move = target - book.balance(dt_acct, t)
                # last period's difference at this period's rate, less at last period's: the remeasurement
                prev = policy.rate if last["rate"] is None else last["rate"]
                rec.update(carrying=carrying, tax_base=tax_base, rate_change=(policy.rate - prev) * last["diff"])
                last["diff"], last["rate"] = carrying - tax_base - losses, policy.rate
            if move > 0:
                g.post(entity, Posting(t, phase, dt_expense, dt_acct, move, {dt_expense: 1, dt_acct: 1},
                                       "tax", f"{ctx['name']}.deferred:{t}"))
            elif move < 0:
                g.post(entity, Posting(t, phase, dt_acct, dt_expense, -move, {dt_acct: -1, dt_expense: -1},
                                       "tax", f"{ctx['name']}.deferred:{t}"))
        g.record(ctx["name"], t, **rec)

    group.register(phase, handler)


RULEBOOKS = {"income_tax": income_tax}
