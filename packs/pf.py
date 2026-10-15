"""Project-finance rulebooks (packs): pro-rata funding, provisions, forgiveness, withholding, promote.

Each rulebook is configuration read from the case file and installed on a
:class:`~one_engine.entities.Group` as a handler for one phase. The
rulebooks hold no arithmetic of their own beyond wiring: dividing an
amount is Allocation, a test is Conditions, a ratio is Measures,
depreciation is Measurement, the base and the loss pool are Tax. That is
the point of the pack layer: Averta's SPV is an assembly, not an engine.

The funding plan, deficit funding, asset register, income tax, waterfall,
capital-first distributions and pay-as-due rulebooks were here until
2026-10-14; three or more archetypes declare each, so they now live in
:mod:`one_engine.rulebooks` (Ali chose the move; ``one_engine/archetypes.yaml``).
The tax instalments rulebook (which no project-finance case uses) and the
loop rules (``sculpt``, ``uses_total``, ``refinance``, ``annuity``,
``level``) followed on 2026-10-15, to :mod:`one_engine.rulebooks.tax` and
:mod:`one_engine.rulebooks.loops`.

Each rulebook is ``rulebook(group, reader, ctx)`` with ``ctx`` holding the
case's currency and classification schemes; it may return named checks
(callables listing failures) that the case runner adds to the verdicts.

Rulebooks
---------
``pro_rata_funding``
    Construction funding as Bodmer's solar model states it (step 7e): a
    budget of total uses less the loan's commitment is the equity
    commitment; a share of it (``equity_first``) is paid in before any
    debt, then each period's remaining uses are split between the loan and
    equity in the ratio of what is left of their commitments, so both are
    exhausted together when the uses equal the budget. The loan's draw is
    a claim (capped at what is undrawn); equity is the named leg.

``provision``
    A liability charged straight-line and paid once
    (:class:`~one_engine.measurement.Provision`): decommissioning.

``forgiveness``
    At a stated period, what is still owed on the listed liabilities (a
    shareholder loan the sponsor will not be repaid) is forgiven: the
    liability falls and a gain is booked. A forgiven debt is never posted
    as a loss; the kernel's direction check refuses that (card 10).

``withholding``
    Registers withholding rates on payments (``senior.interest: 0.1``,
    ``lp.distribute: 0.15``) with :meth:`~one_engine.entities.Group.withhold`
    and remits the payable in full at the end of each listed phase (the
    phases in which withheld payments are made), so a funding phase still
    nets to zero cash. Declare it after the rulebooks that make those
    payments: handlers run in file order within a phase.

``promote``
    Divides the waterfall's residual between two holders (an LP and a GP,
    each a ``sponsor_equity`` contract) as Averta's Phase 11 convention
    states it: return of the LP's capital, then the GP's, then the LP's
    preferred return (simple, on the LP's unreturned capital at the start
    of each period), then a full catch-up to the GP until it holds
    ``carry / (1 - carry)`` of the LP's preferred return, then the rest
    split ``1 - carry`` / ``carry``. ``catchup_pct`` is 1 (that full
    catch-up) or 0 (none: Averta's HoldCo, step 8, whose GP takes its carry
    from the residual split only). Returns of capital go through the
    ``return_capital`` leg (no dividend withholding), the rest through
    ``distribute``. Built on Allocation; the cumulative pref and catch-up
    are this rulebook's state, the unreturned capital is read from the
    books.

Generators
----------
``acquisition``
    An asset bought at a price (an earlier generator's output) in one
    period, a share of it borrowed: the price as a one-period spend, the
    debt and the equity (:func:`acquisition`; Averta's buyer, step 8).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, Mapping

from one_engine.allocation import Tier, allocate
from one_engine.cuts import Scheme
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Book, Nature, Posting
from one_engine.measurement import Provision
from one_engine.rulebooks.allocation import _read_uses, _use_amounts, sources_equal_uses
from one_engine.units import exact


# ── pro-rata funding ─────────────────────────────────────────────────────

def pro_rata_funding(group: Group, r: Reader, ctx: Mapping[str, Any]):
    entity, cash = r.take("entity"), r.take("cash")
    phase, until = r.take("phase"), int(r.take("until"))
    start = int(r.take_or_default("from", 0, reason="funding from the first period").value)
    uses = _read_uses(r, "pro_rata_funding")
    loan, (equity_c, equity_leg) = r.take("loan"), r.take("equity").split(".", 1)
    raw_budget = r.take("budget")
    # the budget may be itemised (the sources and uses table of the source model) or one number
    budget = (sum((exact(v) for v in raw_budget.values()), Fraction(0)) if isinstance(raw_budget, Mapping)
              else exact(raw_budget))
    first = exact(r.take("equity_first"))
    if not 0 <= first <= 1:
        raise InputError(f"pro_rata_funding: equity_first {first} is outside [0, 1]")
    state = {"first_paid": Fraction(0)}       # equity paid in ahead of the debt so far

    def handler(g: Group, t: int) -> None:
        if not start <= t <= until:
            return
        claims = g.claims(t)
        each = _use_amounts(g, t, uses, claims)
        amount = sum(each.values(), Fraction(0))
        if amount == 0:
            return
        debt_commitment = exact(g.contract(loan).values()["commitment"])
        equity_commitment = budget - debt_commitment
        if equity_commitment < 0:
            raise InputError(f"pro_rata_funding: the loan's commitment {float(debt_commitment):,.2f} exceeds "
                             f"the budget {float(budget):,.2f}")
        up_front = first * equity_commitment
        # what is left of both commitments once the up-front equity is in, in the debt's share
        share = debt_commitment / (budget - up_front) if budget > up_front else Fraction(0)
        a = allocate(amount, [Tier("up_front", up_front - state["first_paid"])], residual="pro_rata")
        state["first_paid"] += a.paid["up_front"]
        draw = claims.get(f"{loan}.draw")
        debt = min(share * a.residual, draw.due if draw else Fraction(0))
        if draw is not None:
            g.settle(draw, debt, phase)
        g.post_leg(g.contract(equity_c), equity_leg, amount - debt, t, phase)
        for kind, ref in uses:                      # claims funded here are paid now, in full
            if kind == "claim" and each[ref]:
                g.settle(claims[ref], each[ref], phase)
        g.record("funding", t, uses=dict(each), paid={"up_front": a.paid["up_front"], f"{loan}.draw": debt},
                 residual=amount - debt - a.paid["up_front"], share=share)

    group.register(phase, handler)
    return {"sources_equal_uses": lambda: sources_equal_uses(group, entity, cash, phase)}


# ── provision ─────────────────────────────────────────────────────────────

def provision(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, unit, cash = r.take("entity"), ctx["currency"], r.take("cash")
    charge_phase, pay_phase = r.take("phase"), r.take("pay_phase")
    expense, liability = r.take("expense"), r.take("liability")
    prov = Provision(exact(r.take("amount")), int(r.take("start")), int(r.take("periods")), int(r.take("settle_at")))
    group.declare(entity, expense, Nature.EXPENSE, unit)
    group.declare(entity, liability, Nature.LIABILITY, unit)

    def charge(g: Group, t: int) -> None:
        x = prov.charge(t)
        if x:
            g.post(entity, Posting(t, charge_phase, expense, liability, x, {expense: 1, liability: 1},
                                   "measurement", f"{liability}.charge:{t}"))

    def pay(g: Group, t: int) -> None:
        x = prov.payment(t)
        if x:
            g.post(entity, Posting(t, pay_phase, liability, cash, x, {liability: -1, cash: -1},
                                   "measurement", f"{liability}.payment:{t}"))

    group.register(charge_phase, charge)
    group.register(pay_phase, pay)


# ── forgiveness ───────────────────────────────────────────────────────────

def forgiveness(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, at = r.take("entity"), r.take("phase"), int(r.take("at"))
    accounts, gain = list(r.take("accounts")), r.take("gain")
    group.declare(entity, gain, Nature.INCOME, ctx["currency"])

    def handler(g: Group, t: int) -> None:
        if t != at:
            return
        for acct in accounts:
            x = g.book(entity).balance(acct, t)
            if x:
                # forgiven debt lowers the liability and is a gain, never a loss
                g.post(entity, Posting(t, phase, acct, gain, x, {acct: -1, gain: 1}, "measurement",
                                       f"{acct}.forgiven:{t}"))
                g.record("forgiven", t, account=acct, amount=x)

    group.register(phase, handler)


# ── withholding ───────────────────────────────────────────────────────────

def withholding(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, cash, payable = r.take("entity"), r.take("cash"), r.take("payable")
    phases = list(r.take("phases"))
    group.declare(entity, payable, Nature.LIABILITY, ctx["currency"])
    for ref, rate in dict(r.take("rates")).items():
        group.withhold(ref, exact(rate), payable)

    def remitter(phase):
        def remit(g: Group, t: int) -> None:
            x = g.book(entity).balance(payable, t)
            if x:
                g.post(entity, Posting(t, phase, payable, cash, x, {payable: -1, cash: -1}, "tax",
                                       f"{payable}.remit.{phase}:{t}"))
        return remit

    for phase in phases:
        group.register(phase, remitter(phase))


# ── promote (distributions between two holders) ───────────────────────────

def promote(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    lp, gp = r.take("lp"), r.take("gp")
    accrue_phase = r.take("accrue_phase")
    pref_rate, per_year = exact(r.take("pref_rate")), int(r.take("periods_per_year"))
    compounds, carry = bool(r.take("pref_compounds")), exact(r.take("carry_rate"))
    catchup = exact(r.take("catchup_pct"))
    if catchup not in (0, 1):
        raise InputError("promote: a full (100%) catch-up or none is built")
    if not 0 <= carry < 1:
        raise InputError(f"promote: carry_rate {carry} is outside [0, 1)")
    state = {"accrued": Fraction(0), "pref_paid": Fraction(0), "catchup": Fraction(0)}

    def capital(g, holder, t, opening=False):
        """The holder's unreturned capital: its capital account (returns of capital lower it)."""
        c = g.contract(holder)
        b = g.book(c.parties["issuer"])
        return (b.opening if opening else b.balance)(c.acct("{id}.capital"), t)

    def accrue(g: Group, t: int) -> None:
        # the LP's preferred return accrues on its unreturned capital at the start of the period
        unpaid = state["accrued"] - state["pref_paid"]
        state["accrued"] += (capital(g, lp, t, opening=True) + (unpaid if compounds else 0)) * pref_rate / per_year

    def distribute(g: Group, t: int, amount: Fraction, phase: str) -> None:
        c_pref, c_cu = state["pref_paid"], state["catchup"]

        def catchup_cap(paid):
            # the GP is caught up to carry / (1 - carry) of the LP's profit (pref paid so far); with no
            # catch-up the GP's carry comes from the residual split only
            if not catchup:
                return Fraction(0)
            return max(Fraction(0), carry * (c_pref + paid["pref"]) / (1 - carry) - c_cu)

        a = allocate(amount, [Tier("roc_lp", capital(g, lp, t)), Tier("roc_gp", capital(g, gp, t)),
                               Tier("pref", state["accrued"] - c_pref), Tier("catchup", catchup_cap)],
                     residual="carried")
        state["pref_paid"] += a.paid["pref"]
        state["catchup"] += a.paid["catchup"]
        lp_c, gp_c = g.contract(lp), g.contract(gp)
        g.post_leg(lp_c, "return_capital", a.paid["roc_lp"], t, phase)
        g.post_leg(gp_c, "return_capital", a.paid["roc_gp"], t, phase)
        g.post_leg(lp_c, "distribute", a.paid["pref"] + a.residual * (1 - carry), t, phase)
        g.post_leg(gp_c, "distribute", a.paid["catchup"] + a.residual * carry, t, phase)
        g.record("promote", t, roc_lp=a.paid["roc_lp"], roc_gp=a.paid["roc_gp"], pref=a.paid["pref"],
                 catchup=a.paid["catchup"], residual=a.residual, pref_accrued=state["accrued"])

    group.register(accrue_phase, accrue)
    ctx["distributors"][ctx["name"]] = distribute


RULEBOOKS = {"pro_rata_funding": pro_rata_funding, "provision": provision, "forgiveness": forgiveness,
             "withholding": withholding, "promote": promote}


# ── generators ────────────────────────────────────────────────────────────

def acquisition(r: Reader) -> Dict[str, Any]:
    """An asset bought at ``price`` in period ``at``, a share of it borrowed (a generator).

    Averta's buyer (step 8) pays the portfolio's value at its first period
    and borrows ``debt_share`` of it (the live ``gearing``); the loan's
    commitment, its schedule and the equity are sized on that price, which
    is itself an earlier generator's output, so they follow it in a
    sensitivity. Outputs: ``price``, ``spend`` (the price in period ``at``,
    nil elsewhere, over ``periods``), ``debt`` and ``equity`` (the rest).
    """
    price, share = exact(r.take("price")), exact(r.take("debt_share"))
    at, periods = int(r.take("at")), int(r.take("periods"))
    if not 0 <= share <= 1:
        raise InputError(f"acquisition: debt_share {share} is outside [0, 1]")
    if not 0 <= at < periods:
        raise InputError(f"acquisition: period {at} is outside the {periods} periods")
    return {"price": price, "spend": [price if t == at else Fraction(0) for t in range(periods)],
            "debt": price * share, "equity": price * (1 - share)}


GENERATORS = {"acquisition": acquisition}

