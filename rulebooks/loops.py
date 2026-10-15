"""Loop rules (module card 15): what a declared loop computes from a finished run.

:mod:`one_engine.loops` closes a fixed point across runs; each loop names
one of these rules, which reads the finished run (its records, its books,
a loan's terms) and returns the outputs' next values. Moved from
:mod:`one_engine.packs.pf` on 2026-10-15 (Ali chose to move the shared pieces still in packs; ``one_engine/archetypes.yaml``): project finance, the loans and the
ESCO deals all size debt this way.

Rules
-----
``LOOP_RULES`` are the rules a declared loop (:mod:`one_engine.loops`)
applies to a finished run to get its outputs' next values:

``sculpt``
    Sizes a loan by sculpting: debt service in each repayment period is
    the run's CFADS over the target DSCR, and the debt is that service
    discounted at the loan's own rate, compounded period by period from
    ``repay_from`` (:func:`sculpt`). Outputs ``debt`` and ``repayment``
    (the principal schedule, by period). With CFADS that does not depend
    on the debt (an unlevered tax), two runs close the loop exactly.
``uses_total``
    The total of the uses a funding rulebook funded (its ``funding``
    records): a budget that includes the interest and fees of
    construction, which depend on the debt that the budget sizes.
``refinance``
    Sizes a refinancing loan at what repays an old loan's balance after
    its payment in period ``at``, grossed up for arrangement fees charged
    as rates of the draw; outputs ``debt`` and a ``repayment`` straight-line
    by days (ModelOff 2015, step 7h; :func:`_refinance_rule`).
``annuity``
    The principal schedule of a loan repaid by a constant payment of
    interest and principal, on its own days per period (ModelOff 2014,
    step 7m; :func:`_annuity_rule`); outputs ``repayment``.
``level``
    The principal schedule of a loan repaid in equal steps of what it owes
    (principal plus capitalised interest) when repayment starts, rather
    than of its commitment (ModelOff 2013, step 7n; :func:`_level_rule`);
    outputs ``repayment``.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Mapping

# a loan's term conventions, under the short names the rules were written with
from one_engine.contracts import maturity as _maturity, year_fraction as _days
from one_engine.inputs import Reader
from one_engine.units import at as _at, exact


class SizingError(ValueError):
    """A sculpted size cannot be computed from the run given."""


def sculpt(cfads: Mapping[int, Fraction], rate, dscr: Fraction, first: int, last: int, end: int):
    """Sculpted debt size, principal schedule and discount index.

    Parameters
    ----------
    cfads:
        CFADS by period (at least ``first..last``).
    rate:
        ``rate(t)``: the loan's interest rate for period ``t`` (annual rate
        x year fraction), the same function the loan accrues with.
    dscr:
        The target DSCR: debt service in each period is CFADS / dscr.
    first, last:
        The repayment periods (``repay_from`` to maturity).
    end:
        The last period of the horizon; the index runs to it (a PLCR
        discounts CFADS after maturity too).

    Returns
    -------
    ``(debt, repayment, index)``: the size, the principal of each period
    ``first..last`` (debt service less interest on the sizing balance; it
    is negative where CFADS / dscr is below the interest), and the index
    ``prod (1 + rate)`` from ``first``, by which service is discounted.

    The sizing balance after ``last`` is exactly zero (exact arithmetic);
    a non-zero balance would mean the size and the schedule disagree, and
    is refused.
    """
    index, acc = {}, Fraction(1)
    for t in range(first, end + 1):
        acc *= 1 + rate(t)
        index[t] = acc
    service = {}
    for t in range(first, last + 1):
        if cfads.get(t, Fraction(0)) < 0:
            raise SizingError(f"CFADS is negative in period {t}; a sculpted size would lend against it")
        service[t] = cfads.get(t, Fraction(0)) / dscr
    debt = sum((service[t] / index[t] for t in service), Fraction(0))
    balance, repayment = debt, {}
    for t in range(first, last + 1):
        repayment[t] = service[t] - balance * rate(t)
        balance -= repayment[t]
    if balance != 0:
        raise SizingError(f"the sizing balance ends at {balance}, not zero")
    return debt, repayment, index


def _sculpt_rule(r: Reader):
    loan, dscr, record = r.take("loan"), exact(r.take("dscr")), r.take("cfads")

    def rule(result) -> Dict[str, Any]:
        terms = result.group.contract(loan).values()
        first = int(terms["repay_from"])
        last = first + int(terms["repay_periods"]) - 1
        cfads = {t: rec["cfads"] for t, rec in result.records(record).items()}
        debt, repayment, _ = sculpt(cfads, lambda t: _at(terms["rate"], t) * _days(terms, t), dscr, first, last,
                                    result.group.periods - 1)
        return {"debt": debt, "repayment": [repayment.get(t, Fraction(0)) for t in range(result.group.periods)]}

    return rule


def _uses_total_rule(r: Reader):
    record = r.take("record")

    def rule(result) -> Dict[str, Any]:
        return {"total": sum((sum(rec["uses"].values(), Fraction(0)) for rec in result.records(record).values()),
                             Fraction(0))}

    return rule


def _refinance_rule(r: Reader):
    """A new loan sized to repay what an old one still owes after its payment in period ``at``, net of fees.

    ModelOff 2015 "More Money Please" (step 7h), part A: the refinancing
    loan is drawn at ``at`` for exactly what repays the old loan's balance
    left after that period's scheduled principal, plus the arrangement
    fees, which are rates of the amount drawn (``financing_fee`` contracts
    on the new loan): ``debt = owed / (1 - sum of the fee rates)``. The
    fee makes the size circular, hence a loop; the size does not feed back
    into ``owed``, so the loop closes on its second run. The new loan is
    repaid straight-line by days over its repayment window: each period's
    principal is the size times its ``days_per_period`` over the window's
    total (the case's convention; a half-year at maturity repays half a
    share).

    Outputs ``debt`` (the size) and ``repayment`` (a series, for the new
    loan's ``principal_schedule``).
    """
    old, new, at = r.take("old"), r.take("new"), int(r.take("at"))
    fees, record = list(r.take("fees")), r.take("record")

    def rule(result) -> Dict[str, Any]:
        g = result.group
        old_c, new_terms = g.contract(old), g.contract(new).values()
        rate = Fraction(0)
        for f in fees:
            terms = g.contract(f).values()
            if terms["facility"].id != new:
                raise SizingError(f"refinance: fee '{f}' is on '{terms['facility'].id}', not on the new loan '{new}'")
            rate += exact(terms["rate"])
        if not 0 <= rate < 1:
            raise SizingError(f"refinance: the fees take {rate} of the amount drawn; the loan cannot be sized")
        book = g.book(old_c.parties["borrower"])
        paid = result.records(record)[at]["paid"].get(f"{old}.principal", Fraction(0))
        owed = book.opening(f"{old}.debt", at) - paid
        debt = owed / (1 - rate)
        first, last = int(new_terms["repay_from"]), _maturity(new_terms)
        days = {t: _at(new_terms["days_per_period"], t) for t in range(first, last + 1)}
        total = sum(days.values(), Fraction(0))
        if total == 0:
            raise SizingError(f"refinance: '{new}' has no days in its repayment window")
        return {"debt": debt, "repayment": [debt * days[t] / total if t in days else Fraction(0)
                                            for t in range(g.periods)]}

    return rule


def _annuity_rule(r: Reader):
    """The principal schedule of a loan repaid by equal payments of interest and principal.

    ModelOff 2014 "Precise Debt" (step 7m): a constant monthly payment,
    goal-sought so the loan is repaid exactly by its last payment, with
    interest on the actual days between business-day-adjusted payment
    dates. With ``r_t`` the period's interest rate (``rate`` times the
    loan's ``days_per_period`` over its basis), the payment is
    ``commitment / sum_t prod_{s<=t} 1 / (1 + r_s)`` over the repayment
    window: the closed form the goal seek converges to, exact. Each
    period's principal is the payment less the interest on what is still
    owed. The commitment is taken as fully drawn before ``repay_from``.

    Outputs ``repayment`` (a series, for the loan's ``principal_schedule``);
    the payment is in the books, as each period's interest plus principal.
    The schedule depends on the terms only, so the loop closes on its
    second run.
    """
    loan = r.take("loan")

    def rule(result) -> Dict[str, Any]:
        terms = result.group.contract(loan).values()
        first, last = int(terms["repay_from"]), _maturity(terms)
        rate = {t: (_at(terms["rate"], t) * _days(terms, t) if t >= int(terms["rate_from"]) else Fraction(0))
                for t in range(first, last + 1)}
        factor, total = Fraction(1), Fraction(0)
        for t in range(first, last + 1):
            factor /= 1 + rate[t]
            total += factor
        owed = exact(terms["commitment"])
        payment = owed / total
        repayment = [Fraction(0)] * result.group.periods
        for t in range(first, last + 1):
            repayment[t] = payment - owed * rate[t]
            owed -= repayment[t]
        return {"repayment": repayment}

    return rule


def _level_rule(r: Reader):
    """The principal schedule of a loan repaid in equal steps of what it owes when repayment starts.

    ModelOff 2013 "Acquisition Financing" (step 7n), tranche 4: interest is
    capitalised for two years, then the loan is repaid in 16 equal
    principal payments of the capitalised balance (drawn amount plus the
    capitalised interest). The loan's own equal steps are steps of its
    commitment (the term-loan convention the other cases use), so they
    would repay the capitalised interest first and the steps would not be
    equal. Here the step is what is owed at the start of ``repay_from``
    (principal plus capitalised interest, read from the run's books) over
    ``repay_periods``. That amount depends on the draws and the
    capitalisation window only, not on the schedule, so the loop closes on
    its second run. The loan repays capitalised interest ahead of principal
    (its ``capitalised`` claim); the total paid each period is the step.

    Outputs ``repayment`` (a series, for the loan's ``principal_schedule``).
    """
    loan = r.take("loan")

    def rule(result) -> Dict[str, Any]:
        g = result.group
        c = g.contract(loan)
        terms, book = c.values(), g.book(c.parties["borrower"])
        first, last = int(terms["repay_from"]), _maturity(terms)
        owed = book.opening(f"{loan}.debt", first) + book.opening(f"{loan}.capitalised_interest", first)
        step = owed / int(terms["repay_periods"])
        return {"repayment": [step if first <= t <= last else Fraction(0) for t in range(g.periods)]}

    return rule


LOOP_RULES = {"sculpt": _sculpt_rule, "uses_total": _uses_total_rule, "refinance": _refinance_rule,
              "annuity": _annuity_rule, "level": _level_rule}
