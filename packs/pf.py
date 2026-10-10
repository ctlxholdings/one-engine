"""Project-finance rulebooks (packs): funding plan, asset register, income tax, waterfall.

Each rulebook is configuration read from the case file and installed on a
:class:`~one_engine.entities.Group` as a handler for one phase. The
rulebooks hold no arithmetic of their own beyond wiring: dividing an
amount is Allocation, a test is Conditions, a ratio is Measures,
depreciation is Measurement, the base and the loss pool are Tax. That is
the point of the pack layer: Averta's SPV is an assembly, not an engine.

Each rulebook is ``rulebook(group, reader, ctx)`` with ``ctx`` holding the
case's currency and classification schemes; it may return named checks
(callables listing failures) that the case runner adds to the verdicts.

Rulebooks
---------
``funding_plan``
    Construction funding, through period ``until``. Uses are named
    contract flows of the period (``epc.spend``, ``dsra.prefund``) or
    claims due (construction interest paid in cash). Sources are loan
    draw claims, each capped at a share of the uses (or of the uses listed
    in its ``of``) and at what remains of its commitment, in order; the named residual (``equity.contribute``) closes the gap.
    The residual may be split between holders by shares (two equity
    holders). ``from`` (default 0) and ``until`` bound the periods.
    Sources therefore equal uses at each step, and
    :func:`sources_equal_uses` re-checks that from the books. The old
    engine called equity on a schedule instead and left 23.6M of idle cash
    on the 100MW benchmark (step 6 finding).

``pro_rata_funding``
    Construction funding as Bodmer's solar model states it (step 7e): a
    budget of total uses less the loan's commitment is the equity
    commitment; a share of it (``equity_first``) is paid in before any
    debt, then each period's remaining uses are split between the loan and
    equity in the ratio of what is left of their commitments, so both are
    exhausted together when the uses equal the budget. The loan's draw is
    a claim (capped at what is undrawn); equity is the named leg.

``asset_register``
    At each in-service period (COD, a major maintenance), CWIP moves to
    fixed assets and becomes a tranche of the register; each period's
    charge and the disposal at ``dispose_at`` come from
    :class:`~one_engine.measurement.Register`. ``first_charge`` is
    required. An optional ``tax`` register depreciates the same tranches
    over the tax life. With ``weights`` (one per period) in place of
    ``life_periods`` (stated null), each tranche is spread over the rest of
    the weights (``Policy.weights``: ModelOff's days of operation left,
    step 7h). Fixed assets on the opening balance sheet are a tranche from
    the first in-service period. Either register may instead take a
    ``declining_rate`` (``Policy.declining_rate``: ModelOff 2012's 40% tax
    depreciation, step 7i); exactly one of the three is given.

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

    ``losses_in: tax`` carries losses as tax, at the rate of the period
    they arose in (``TaxPolicy.losses_in``: ModelOff 2015 "Tax", step 7k);
    the tax record's ``expired`` is what the period could use but the next
    cannot.

``tax_instalments``
    Last year's tax, paid this year in instalments: each listed period of
    the year (``period_of_year``, 0 being the first) pays its ``share`` of
    everything charged to the payable in the previous year's periods. The
    grid's years start at period 0, ``periods_per_year`` long. The shares
    sum to one, so a year's liability is paid in full once its last
    instalment falls inside the grid (ModelOff 2015 "Tax", step 7k).

``pay_as_due``
    The listed claims paid in full when due, whatever the cash: for a case
    that lets cash go negative to show a shortfall (ModelOff 2013 "Hard
    Times", step 7l). The overdraft that implies is not modelled, so cash
    is left unfloored; a waterfall would pay only from cash above the floor.

``provision``
    A liability charged straight-line and paid once
    (:class:`~one_engine.measurement.Provision`): decommissioning.

``forgiveness``
    At a stated period, what is still owed on the listed liabilities (a
    shareholder loan the sponsor will not be repaid) is forgiven: the
    liability falls and a gain is booked. A forgiven debt is never posted
    as a loss; the kernel's direction check refuses that (card 10).

``waterfall``
    In the pay phase, all cash above the floor is allocated over ordered
    tiers: contract claims (``senior.interest``) or payable accounts
    (``tax_payable``). DSCR is CFADS over the debt service due, where
    CFADS is the period's cash flow in the named classes less the named
    tiers (tax), times an optional ``share`` by period (a series: the part
    of the period the debt is outstanding, ModelOff step 7h; one by
    default). A minimum balance is a ``keep`` tier, a stock kept back in
    the stated periods at its place in the order (after debt service, so a
    minimum balance never makes a senior payment late), not a deduction
    from each period's flow. Each test
    blocks the tiers it lists when it fails, or only reports. The residual goes to its leg (a distribution) unless a
    test blocks it; blocked cash stays in the account and is offered again
    next period, which is how trapped cash is released when a lock-up
    clears. Unpaid claims follow their contract: arrears, or PIK.
    ``sources`` (reserve releases) are taken into cash before anything is
    paid. CFADS is passed to every claim (``given``), which is how a
    sculpted loan reads it. A tier with ``share_of_remaining`` takes at
    most that share of what is still unallocated when its turn comes (a
    mezzanine cash sweep). The residual may name a distributor rulebook
    (a promote) instead of a leg, or be null: the company retains it, and
    it stays in the account (Macabacus LBO, step 7f: no common dividends).

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
    split ``1 - carry`` / ``carry``. Returns of capital go through the
    ``return_capital`` leg (no dividend withholding), the rest through
    ``distribute``. Built on Allocation; the cumulative pref and catch-up
    are this rulebook's state, the unreturned capital is read from the
    books.

Loop rules
----------
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
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Mapping

from one_engine.allocation import Tier, allocate
from one_engine.conditions import Test, evaluate
from one_engine.cuts import Scheme, cash_flow_cut
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Book, Nature, Posting
from one_engine.measurement import Policy, Provision, Register
from one_engine.measures import coverage
from one_engine.tax import LossPool, TaxPolicy, deferred_tax, taxable_base
from one_engine.units import exact


def _ref(group: Group, ref: str):
    cid, _, part = ref.partition(".")
    return group.contract(cid), part


# ── funding plan ──────────────────────────────────────────────────────────

def _residual_legs(raw, where):
    """A residual names one leg, or several legs with shares summing to one (equity held by two holders)."""
    if isinstance(raw, str):
        return [(raw, Fraction(1))]
    legs = []
    for i, item in enumerate(raw):
        rr = Reader(item, source=f"{where}[{i}]")
        legs.append((rr.take("leg"), exact(rr.take("share"))))
        rr.finish()
    if sum(s for _, s in legs) != 1 or any(s < 0 for _, s in legs):
        raise InputError(f"{where}: residual shares must be non-negative and sum to 1")
    return legs


def _post_residual(g: Group, legs, amount, t: int, phase: str) -> None:
    for ref, share in legs:
        c, leg = _ref(g, ref)
        g.post_leg(c, leg, amount * share, t, phase)


def _read_uses(r: Reader, where: str):
    """A funding rulebook's uses: (``claim`` or ``flow``, reference), in file order."""
    uses = []
    for i, raw in enumerate(r.take("uses")):
        ur = Reader(raw, source=f"{where}.uses[{i}]")
        kind = "claim" if "claim" in raw else "flow"
        uses.append((kind, ur.take(kind)))
        ur.finish()
    return uses


def _use_amounts(g: Group, t: int, uses, claims) -> Dict[str, Fraction]:
    """Each use's amount in ``t``: the claim due, or the contract flow of that name."""
    each = {}
    for kind, ref in uses:
        if kind == "claim":
            each[ref] = claims[ref].due if ref in claims else Fraction(0)
        else:
            c, flow = _ref(g, ref)
            each[ref] = sum((f.amount for f in c.flows(t, g.view(c, t)) if f.spec.name == flow), Fraction(0))
    return each


def funding_plan(group: Group, r: Reader, ctx: Mapping[str, Any]):
    entity, cash = r.take("entity"), r.take("cash")
    phase, until = r.take("phase"), int(r.take("until"))
    start = int(r.take_or_default("from", 0, reason="funding from the first period").value)
    uses = _read_uses(r, "funding_plan")
    sources = []
    for i, raw in enumerate(r.take("sources")):
        sr = Reader(raw, source=f"funding_plan.sources[{i}]")
        base = sr.take_or_default("of", [ref for _, ref in uses], reason="share of all uses").value
        sources.append((sr.take("claim"), exact(sr.take("share")), list(base)))
        sr.finish()
    residual = _residual_legs(r.take("residual"), "funding_plan.residual")

    def handler(g: Group, t: int) -> None:
        if not start <= t <= until:
            return
        claims = g.claims(t)
        each = _use_amounts(g, t, uses, claims)
        amount = sum(each.values(), Fraction(0))
        if amount == 0:
            return
        tiers = [Tier(ref, min(share * sum((each[u] for u in base), Fraction(0)), claims[ref].due) if ref in claims else 0)
                 for ref, share, base in sources]
        a = allocate(amount, tiers, residual="equity")
        for ref, _, _ in sources:
            if ref in claims:
                g.settle(claims[ref], a.paid[ref], phase)
        _post_residual(g, residual, a.residual, t, phase)
        for kind, ref in uses:                      # claims funded here are paid now, in full
            if kind == "claim" and each[ref]:
                g.settle(claims[ref], each[ref], phase)
        g.record("funding", t, uses=dict(each), paid=dict(a.paid), residual=a.residual)

    group.register(phase, handler)
    return {"sources_equal_uses": lambda: sources_equal_uses(group, entity, cash, phase)}


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


def sources_equal_uses(group: Group, entity: str, cash: str, phase: str) -> List[int]:
    """Periods whose funding phase leaves a net cash movement (sources differ from uses)."""
    net: Dict[int, Fraction] = {}
    for p in group.book(entity).postings:
        if p.phase == phase and cash in (p.debit, p.credit):
            net[p.period] = net.get(p.period, Fraction(0)) + (p.amount if p.debit == cash else -p.amount)
    return sorted(t for t, v in net.items() if v != 0)


# ── asset register ────────────────────────────────────────────────────────

class AssetRegisters:
    """A rulebook's book register and, if declared, the tax authority's register of the same assets.

    Shared through ``ctx["registers"]`` under the rulebook's name, so the
    income tax rulebook can replace book depreciation by tax depreciation
    and measure the temporary difference.
    """

    def __init__(self, book: Register, tax, expense: str, disposal: str):
        self.book, self.tax, self.expense, self.disposal = book, tax, expense, disposal
        self.tax_moves: Dict[int, Fraction] = {}       # period -> tax depreciation + tax disposal


def _policy(r: Reader, where: str) -> Policy:
    """A depreciation policy: ``life_periods`` (stated, null when another basis is given), or ``weights``
    by period, or a ``declining_rate``; exactly one, and the first-charge convention."""
    life = r.take("life_periods")
    weights = r.take_or_default("weights", None, reason="no weights by period").value
    declining = r.take_or_default("declining_rate", None, reason="not declining balance").value
    if sum(x is not None for x in (life, weights, declining)) != 1:
        raise InputError(f"{where}: give life_periods, weights or declining_rate (life_periods: null), exactly one")
    return Policy(None if life is None else int(life), r.take("first_charge"), weights,
                  None if declining is None else exact(declining))


def asset_register(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    src, fa, dep, loss = r.take("from"), r.take("to"), r.take("expense"), r.take("disposal")
    raw_in = r.take("in_service")
    in_service = [int(x) for x in raw_in] if isinstance(raw_in, list) else [int(raw_in)]
    dispose_at = int(r.take("dispose_at"))
    reg = Register(_policy(r, "asset_register"))
    tax_raw = r.take_or_default("tax", None, reason="the tax authority depreciates as the books do").value
    tax_reg = None
    if tax_raw is not None:
        tr = Reader(tax_raw, source="asset_register.tax")
        tax_reg = Register(_policy(tr, "asset_register.tax"))
        tr.finish()
    regs = AssetRegisters(reg, tax_reg, dep, loss)
    ctx["registers"][ctx["name"]] = regs
    for name, nature in ((fa, Nature.ASSET), (dep, Nature.EXPENSE), (loss, Nature.EXPENSE)):
        group.declare(entity, name, nature, unit)

    def post(g, t, debit, credit, amount, event):
        # every posting here raises a debit-natured account and lowers another
        if amount:
            g.post(entity, Posting(t, phase, debit, credit, amount, {debit: 1, credit: -1}, "measurement", f"assets.{event}:{t}"))

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        if t == in_service[0] and book.opening(fa, t):
            # fixed assets carried in on an opening balance sheet are a tranche from the first in-service
            # period: otherwise the register would never depreciate them
            for x in (reg, tax_reg):
                if x is not None:
                    x.add(t, book.opening(fa, t))
        if t in in_service:
            cost = book.balance(src, t)
            post(g, t, fa, src, cost, "in_service")
            for x in (reg, tax_reg):
                if x is not None:
                    x.add(t, cost)
        if t >= in_service[0]:
            post(g, t, dep, fa, reg.charge(t), "depreciation")
            if tax_reg is not None:
                regs.tax_moves[t] = tax_reg.charge(t)
        if t == dispose_at:
            post(g, t, loss, fa, reg.dispose(), "disposal")
            if tax_reg is not None:
                regs.tax_moves[t] = regs.tax_moves.get(t, Fraction(0)) + tax_reg.dispose()

    group.register(phase, handler)


# ── income tax ────────────────────────────────────────────────────────────

def income_tax(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    expense, payable = r.take("expense"), r.take("payable")
    start = int(r.take_or_default("from", 0, reason="tax is charged from the first period").value)
    cf = r.take("carry_forward_periods")
    cf = None if cf is None else int(cf)
    rates = r.take("rate")                      # a rate, or one per period (a change of rate)
    losses_in = r.take_or_default("losses_in", "base", reason="losses are carried as amounts of the base").value
    from one_engine.packs.contracts import _at
    scheme = dict(r.take("scheme"))
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
        policy = TaxPolicy(_at(rates, t), cf, losses_in)
        base = taxable_base(g.book(entity), t, scheme)
        if timing_name is not None:
            regs = ctx["registers"][timing_name]
            if timing_lines != sorted({regs.expense, regs.disposal}):
                raise InputError(f"income_tax: 'timing' lines {timing_lines} are not the lines of register "
                                 f"'{timing_name}' ({regs.expense}, {regs.disposal})")
            base -= regs.tax_moves.get(t, Fraction(0))
        tax = policy.charge(t, base, pool)
        if tax:
            g.post(entity, Posting(t, phase, expense, payable, tax, {expense: 1, payable: 1}, "tax", f"tax.charge:{t}"))
        # what this period could still use but the next cannot: the vintages that expire at its end
        rec = dict(base=base, tax=tax, losses=pool.available(t + 1), expired=pool.available(t) - pool.available(t + 1))
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
                                       "tax", f"tax.deferred:{t}"))
            elif move < 0:
                g.post(entity, Posting(t, phase, dt_acct, dt_expense, -move, {dt_acct: -1, dt_expense: -1},
                                       "tax", f"tax.deferred:{t}"))
        g.record("tax", t, **rec)

    group.register(phase, handler)


# ── tax instalments ───────────────────────────────────────────────────────

def tax_instalments(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, cash, payable = r.take("entity"), r.take("phase"), r.take("cash"), r.take("payable")
    per_year = int(r.take("periods_per_year"))
    plan = []
    for i, raw in enumerate(r.take("instalments")):
        ir = Reader(raw, source=f"tax_instalments.instalments[{i}]")
        plan.append((int(ir.take("period_of_year")), exact(ir.take("share"))))
        ir.finish()
    if sum(x for _, x in plan) != 1 or any(x < 0 for _, x in plan) or \
            any(not 0 <= k < per_year for k, _ in plan) or len({k for k, _ in plan}) != len(plan):
        raise InputError(f"tax_instalments: shares must be non-negative and sum to 1, in distinct periods of the "
                         f"year 0..{per_year - 1}; got {[(k, float(x)) for k, x in plan]}")
    shares = dict(plan)

    def handler(g: Group, t: int) -> None:
        year, k = divmod(t, per_year)
        if year == 0 or k not in shares:
            return
        book = g.book(entity)
        # last year's liability: everything charged to the payable in its periods
        charged = sum((p.amount for p in book.postings
                       if p.credit == payable and (year - 1) * per_year <= p.period < year * per_year), Fraction(0))
        amount = charged * shares[k]
        if amount:
            g.post(entity, Posting(t, phase, payable, cash, amount, {payable: -1, cash: -1}, "tax",
                                   f"tax.instalment:{t}"))
        g.record("tax_instalment", t, liability=charged, paid=amount)

    group.register(phase, handler)


# ── pay as due ────────────────────────────────────────────────────────────

def pay_as_due(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    phase, refs = r.take("phase"), list(r.take("claims"))

    def handler(g: Group, t: int) -> None:
        claims = g.claims(t)
        for ref in refs:
            if ref in claims and claims[ref].due:
                g.settle(claims[ref], claims[ref].due, phase)

    group.register(phase, handler)


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


# ── waterfall ─────────────────────────────────────────────────────────────

def waterfall(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, cash = r.take("entity"), r.take("phase"), r.take("cash")
    sources = list(r.take_or_default("sources", [], reason="no reserve releases into the waterfall").value)
    tiers_spec = []          # (name, kind, ref, share_of_remaining)
    for i, raw in enumerate(r.take("tiers")):
        tr = Reader(raw, source=f"waterfall.tiers[{i}]")
        if "claim" in raw:
            name = kind_ref = tr.take("claim")
            kind = "claim"
        elif "keep" in raw:
            # a minimum cash balance: what this tier is "paid" stays in the account
            name, kind = tr.take("name"), "keep"
            kind_ref = (exact(tr.take("keep")), int(tr.take("from")), int(tr.take("until")))
        else:
            name, kind, kind_ref = tr.take("name"), "account", tr.take("account")
        share = tr.take_or_default("share_of_remaining", None, reason="the tier takes all it is due").value
        tiers_spec.append((name, kind, kind_ref, None if share is None else exact(share)))
        tr.finish()
    names = [n for n, _, _, _ in tiers_spec]
    residual = r.take("residual")
    cr = Reader(r.take("cfads"), source="waterfall.cfads")
    scheme, classes, less = ctx["schemes"][cr.take("scheme")], cr.take("classes"), cr.take("less")
    share = cr.take_or_default("share", 1, reason="the debt is outstanding for the whole of each period").value
    cr.finish()
    service = r.take("service")
    tests = []
    for i, raw in enumerate(r.take("tests")):
        tr = Reader(raw, source=f"waterfall.tests[{i}]")
        test = Test(tr.take("name"), tr.take("op"), exact(tr.take("level")), tr.take("if_missing"))
        consequence = tr.take("consequence")
        blocks = tr.take("blocks") if consequence == "block" else []
        if consequence not in ("block", "report"):
            raise InputError(f"test '{test.name}': consequence must be 'block' or 'report'")
        unknown = set(blocks) - set(names) - {residual}
        if unknown:
            raise InputError(f"test '{test.name}' blocks unknown tiers {sorted(unknown)}")
        tests.append((test, consequence, blocks))
        tr.finish()
    for n in list(less) + list(service):
        if n not in names:
            raise InputError(f"waterfall: '{n}' is not a tier")
    for n in less:
        if dict((x[0], x[1]) for x in tiers_spec)[n] != "account":
            # CFADS is computed before claims are read, because a sculpted loan's claim needs it
            raise InputError(f"waterfall: '{n}' in cfads.less must be an account tier")

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        due = {name: book.balance(ref, t) for name, kind, ref, _ in tiers_spec if kind == "account"}
        flows = cash_flow_cut(book, cash, scheme, classes).values[t - book.start_period]
        cfads = flows - sum((due[n] for n in less), Fraction(0))
        if share != 1:
            # the part of the period's cash flow that falls while the debt is outstanding (a loan that
            # matures mid-year is covered by the cash flow of its days only)
            from one_engine.packs.contracts import _at
            cfads *= _at(share, t)
        claims = g.claims(t, given={"cfads": cfads})
        for ref in sources:                         # reserve releases come in before anything is paid
            if ref in claims:
                g.settle(claims[ref], claims[ref].due, phase)
        for name, kind, ref, _ in tiers_spec:
            if kind == "claim":
                due[name] = claims[ref].due if ref in claims else Fraction(0)
            elif kind == "keep":
                due[name] = ref[0] if ref[1] <= t <= ref[2] else Fraction(0)
        ds = sum((due[n] for n in service), Fraction(0))
        dscr = coverage(cfads, ds)
        blocked: Dict[str, str] = {}
        for test, consequence, blocks in tests:
            res = evaluate(test, dscr)
            g.record("test", t, test=test.name, verdict=res.verdict, value=dscr, level=test.level,
                     consequence=consequence)
            if res.verdict == "fail":
                for b in blocks:
                    blocked.setdefault(b, test.name)
        floor = exact(g.floors.get((entity, cash), Fraction(0)))
        available = max(Fraction(0), book.balance(cash, t) - floor)

        def cap(name, share):
            if share is None:
                return due[name]
            # a sweep: a share of what is still unallocated when its turn comes
            return lambda paid: min(due[name], share * (available - sum(paid.values(), Fraction(0))))

        a = allocate(available, [Tier(n, cap(n, sh), blocked_by=blocked.get(n)) for n, _, _, sh in tiers_spec],
                     residual=residual or "retained")
        for name, kind, ref, _ in tiers_spec:
            if kind == "claim":
                if ref in claims:
                    g.settle(claims[ref], a.paid[name], phase)
            elif kind == "account" and a.paid[name]:
                g.post(entity, Posting(t, phase, ref, cash, a.paid[name], {ref: -1, cash: -1}, "allocation",
                                       f"waterfall.{name}:{t}"))
        if residual is None:
            pass                                    # retained: the cash stays where it is
        elif residual in blocked:
            g.record("trapped", t, amount=a.residual, by=blocked[residual])
        elif residual in ctx["distributors"]:
            ctx["distributors"][residual](g, t, a.residual, phase)
        else:
            c, leg = _ref(g, residual)
            g.post_leg(c, leg, a.residual, t, phase)
        g.record("waterfall", t, cfads=cfads, service=ds, dscr=dscr, available=available,
                 paid=dict(a.paid), residual=a.residual, blocked=dict(blocked))

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
    if exact(r.take("catchup_pct")) != 1:
        raise InputError("promote: only a full (100%) catch-up is built")
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
            # the GP is caught up to carry / (1 - carry) of the LP's profit (pref paid so far)
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


RULEBOOKS = {"funding_plan": funding_plan, "pro_rata_funding": pro_rata_funding, "asset_register": asset_register,
             "income_tax": income_tax, "provision": provision, "forgiveness": forgiveness, "waterfall": waterfall,
             "withholding": withholding, "promote": promote, "tax_instalments": tax_instalments,
             "pay_as_due": pay_as_due}


# ── loop rules ────────────────────────────────────────────────────────────

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
    from one_engine.packs.contracts import _at, _days
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
    from one_engine.packs.contracts import _at, _maturity
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
    from one_engine.packs.contracts import _at, _days, _maturity
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


LOOP_RULES = {"sculpt": _sculpt_rule, "uses_total": _uses_total_rule, "refinance": _refinance_rule,
              "annuity": _annuity_rule}
