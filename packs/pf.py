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

``asset_register``
    At each in-service period (COD, a major maintenance), CWIP moves to
    fixed assets and becomes a tranche of the register; each period's
    charge and the disposal at ``dispose_at`` come from
    :class:`~one_engine.measurement.Register`. ``first_charge`` is
    required. An optional ``tax`` register depreciates the same tranches
    over the tax life.

``income_tax``
    The base comes from a scheme classifying every profit-and-loss account
    of the book (:func:`~one_engine.tax.taxable_base`). The charge goes to
    ``tax_payable``; the waterfall pays it. With ``timing: <register>``,
    the lines classified ``timing`` (book depreciation and disposal) are
    replaced in the base by that register's tax depreciation. With
    ``deferred``, the net deferred tax balance
    (:func:`~one_engine.tax.deferred_tax`, on the registers' NBVs and the
    losses carried forward) is kept in its account, its movement charged
    to a deferred tax expense line.

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
    tiers (tax). A minimum balance is a ``keep`` tier, a stock kept back in
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
    (a promote) instead of a leg.

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


def funding_plan(group: Group, r: Reader, ctx: Mapping[str, Any]):
    entity, cash = r.take("entity"), r.take("cash")
    phase, until = r.take("phase"), int(r.take("until"))
    start = int(r.take_or_default("from", 0, reason="funding from the first period").value)
    uses = []
    for i, raw in enumerate(r.take("uses")):
        ur = Reader(raw, source=f"funding_plan.uses[{i}]")
        kind = "claim" if "claim" in raw else "flow"
        uses.append((kind, ur.take(kind)))
        ur.finish()
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
        each = {}
        for kind, ref in uses:
            if kind == "claim":
                each[ref] = claims[ref].due if ref in claims else Fraction(0)
            else:
                c, flow = _ref(g, ref)
                each[ref] = sum((f.amount for f in c.flows(t, g.view(c, t)) if f.spec.name == flow), Fraction(0))
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


def asset_register(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    src, fa, dep, loss = r.take("from"), r.take("to"), r.take("expense"), r.take("disposal")
    raw_in = r.take("in_service")
    in_service = [int(x) for x in raw_in] if isinstance(raw_in, list) else [int(raw_in)]
    dispose_at = int(r.take("dispose_at"))
    reg = Register(Policy(int(r.take("life_periods")), r.take("first_charge")))
    tax_raw = r.take_or_default("tax", None, reason="the tax authority depreciates as the books do").value
    tax_reg = None
    if tax_raw is not None:
        tr = Reader(tax_raw, source="asset_register.tax")
        tax_reg = Register(Policy(int(tr.take("life_periods")), tr.take("first_charge")))
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
    cf = r.take("carry_forward_periods")
    policy = TaxPolicy(exact(r.take("rate")), None if cf is None else int(cf))
    scheme = dict(r.take("scheme"))
    group.declare(entity, expense, Nature.EXPENSE, unit)
    group.declare(entity, payable, Nature.LIABILITY, unit)
    pool = LossPool(policy.carry_forward_periods)
    timing_name = r.take_or_default("timing", None, reason="no line is measured differently for tax").value
    deferred = r.take_or_default("deferred", None, reason="no deferred tax recognised").value
    timing_lines = sorted(k for k, v in scheme.items() if v == "timing")
    if (timing_name is None) != (not timing_lines):
        raise InputError("income_tax: lines classified 'timing' need a 'timing' register, and only then")
    if deferred is not None:
        dr = Reader(deferred, source="income_tax.deferred")
        dt_acct, dt_expense = dr.take("account"), dr.take("expense")
        dr.finish()
        group.declare(entity, dt_acct, Nature.LIABILITY, unit)
        group.declare(entity, dt_expense, Nature.EXPENSE, unit)

    def handler(g: Group, t: int) -> None:
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
        g.record("tax", t, base=base, tax=tax, losses=pool.available(t + 1))
        if deferred is not None:
            regs = ctx["registers"][timing_name] if timing_name else None
            carrying = regs.book.nbv() if regs else Fraction(0)
            tax_base = regs.tax.nbv() if regs else Fraction(0)
            target = deferred_tax(policy.rate, carrying, tax_base, pool.available(t + 1))
            move = target - g.book(entity).balance(dt_acct, t)
            if move > 0:
                g.post(entity, Posting(t, phase, dt_expense, dt_acct, move, {dt_expense: 1, dt_acct: 1},
                                       "tax", f"tax.deferred:{t}"))
            elif move < 0:
                g.post(entity, Posting(t, phase, dt_acct, dt_expense, -move, {dt_acct: -1, dt_expense: -1},
                                       "tax", f"tax.deferred:{t}"))

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
                     residual=residual)
        for name, kind, ref, _ in tiers_spec:
            if kind == "claim":
                if ref in claims:
                    g.settle(claims[ref], a.paid[name], phase)
            elif kind == "account" and a.paid[name]:
                g.post(entity, Posting(t, phase, ref, cash, a.paid[name], {ref: -1, cash: -1}, "allocation",
                                       f"waterfall.{name}:{t}"))
        if residual in blocked:
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


RULEBOOKS = {"funding_plan": funding_plan, "asset_register": asset_register, "income_tax": income_tax,
             "provision": provision, "forgiveness": forgiveness, "waterfall": waterfall,
             "withholding": withholding, "promote": promote}
