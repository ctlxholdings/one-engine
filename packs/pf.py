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
    Construction funding. Uses are named contract flows of the period
    (``epc.spend``, ``dsra.prefund``). Sources are loan draw claims, each
    capped at a share of the uses (or of the uses listed in its ``of``)
    and at what remains of its commitment, in order; the named residual (``equity.contribute``) closes the gap.
    Sources therefore equal uses at each step, and
    :func:`sources_equal_uses` re-checks that from the books. The old
    engine called equity on a schedule instead and left 23.6M of idle cash
    on the 100MW benchmark (step 6 finding).

``asset_register``
    At the in-service period, CWIP moves to fixed assets and becomes a
    tranche of the register; each period's charge and the disposal at
    ``dispose_at`` come from :class:`~one_engine.measurement.Register`.
    ``first_charge`` is required.

``income_tax``
    The base comes from a scheme classifying every profit-and-loss account
    of the book (:func:`~one_engine.tax.taxable_base`). The charge goes to
    ``tax_payable``; the waterfall pays it.

``waterfall``
    In the pay phase, all cash above the floor is allocated over ordered
    tiers: contract claims (``senior.interest``) or payable accounts
    (``tax_payable``). DSCR is CFADS over the debt service due, where
    CFADS is the period's cash flow in the named classes less the named
    tiers (tax). Each test blocks the tiers it lists when it fails, or
    only reports. The residual goes to its leg (a distribution) unless a
    test blocks it; blocked cash stays in the account and is offered again
    next period, which is how trapped cash is released when a lock-up
    clears. Unpaid claims follow their contract: arrears, or PIK.
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
from one_engine.measurement import Policy, Register
from one_engine.measures import coverage
from one_engine.tax import LossPool, TaxPolicy, taxable_base
from one_engine.units import exact


def _ref(group: Group, ref: str):
    cid, _, part = ref.partition(".")
    return group.contract(cid), part


# ── funding plan ──────────────────────────────────────────────────────────

def funding_plan(group: Group, r: Reader, ctx: Mapping[str, Any]):
    entity, cash = r.take("entity"), r.take("cash")
    phase, uses = r.take("phase"), list(r.take("uses"))
    sources = []
    for i, raw in enumerate(r.take("sources")):
        sr = Reader(raw, source=f"funding_plan.sources[{i}]")
        base = sr.take_or_default("of", uses, reason="share of all uses").value
        sources.append((sr.take("claim"), exact(sr.take("share")), list(base)))
        sr.finish()
    residual = r.take("residual")

    def handler(g: Group, t: int) -> None:
        def flow_amount(ref):
            c, flow = _ref(g, ref)
            return sum((f.amount for f in c.flows(t, g.view(c, t)) if f.spec.name == flow), Fraction(0))
        each = {ref: flow_amount(ref) for ref in uses}
        amount = sum(each.values(), Fraction(0))
        if amount == 0:
            return
        claims = g.claims(t)
        tiers = [Tier(ref, min(share * sum((each[u] for u in base), Fraction(0)), claims[ref].due) if ref in claims else 0)
                 for ref, share, base in sources]
        a = allocate(amount, tiers, residual=residual)
        for ref, _, _ in sources:
            if ref in claims:
                g.settle(claims[ref], a.paid[ref], phase)
        c, leg = _ref(g, residual)
        g.post_leg(c, leg, a.residual, t, phase)
        g.record("funding", t, uses=amount, paid=dict(a.paid), residual=a.residual)

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

def asset_register(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, unit = r.take("entity"), r.take("phase"), ctx["currency"]
    src, fa, dep, loss = r.take("from"), r.take("to"), r.take("expense"), r.take("disposal")
    in_service, dispose_at = int(r.take("in_service")), int(r.take("dispose_at"))
    reg = Register(Policy(int(r.take("life_periods")), r.take("first_charge")))
    for name, nature in ((fa, Nature.ASSET), (dep, Nature.EXPENSE), (loss, Nature.EXPENSE)):
        group.declare(entity, name, nature, unit)

    def post(g, t, debit, credit, amount, event):
        # every posting here raises a debit-natured account and lowers another
        if amount:
            g.post(entity, Posting(t, phase, debit, credit, amount, {debit: 1, credit: -1}, "measurement", f"assets.{event}:{t}"))

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        if t == in_service:
            cost = book.balance(src, t)
            post(g, t, fa, src, cost, "in_service")
            reg.add(t, cost)
        if t >= in_service:
            post(g, t, dep, fa, reg.charge(t), "depreciation")
        if t == dispose_at:
            post(g, t, loss, fa, reg.dispose(), "disposal")

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

    def handler(g: Group, t: int) -> None:
        base = taxable_base(g.book(entity), t, scheme)
        tax = policy.charge(t, base, pool)
        if tax:
            g.post(entity, Posting(t, phase, expense, payable, tax, {expense: 1, payable: 1}, "tax", f"tax.charge:{t}"))
        g.record("tax", t, base=base, tax=tax, losses=pool.available(t + 1))

    group.register(phase, handler)


# ── waterfall ─────────────────────────────────────────────────────────────

def waterfall(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, phase, cash = r.take("entity"), r.take("phase"), r.take("cash")
    tiers_spec = []
    for i, raw in enumerate(r.take("tiers")):
        tr = Reader(raw, source=f"waterfall.tiers[{i}]")
        if "claim" in raw:
            tiers_spec.append((tr.take("claim"), "claim", tr.take("claim")))
        else:
            tiers_spec.append((tr.take("name"), "account", tr.take("account")))
        tr.finish()
    names = [n for n, _, _ in tiers_spec]
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

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        claims = g.claims(t)
        due = {}
        for name, kind, ref in tiers_spec:
            due[name] = (claims[ref].due if ref in claims else Fraction(0)) if kind == "claim" else book.balance(ref, t)
        flows = cash_flow_cut(book, cash, scheme, classes).values[t - book.start_period]
        cfads = flows - sum((due[n] for n in less), Fraction(0))
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
        floor = g.floors.get((entity, cash), Fraction(0))
        available = book.balance(cash, t) - exact(floor)
        a = allocate(available, [Tier(n, due[n], blocked_by=blocked.get(n)) for n in names], residual=residual)
        for name, kind, ref in tiers_spec:
            if kind == "claim":
                if ref in claims:
                    g.settle(claims[ref], a.paid[name], phase)
            elif a.paid[name]:
                g.post(entity, Posting(t, phase, ref, cash, a.paid[name], {ref: -1, cash: -1}, "allocation",
                                       f"waterfall.{name}:{t}"))
        if residual in blocked:
            g.record("trapped", t, amount=a.residual, by=blocked[residual])
        else:
            c, leg = _ref(g, residual)
            g.post_leg(c, leg, a.residual, t, phase)
        g.record("waterfall", t, cfads=cfads, service=ds, dscr=dscr, available=available,
                 paid=dict(a.paid), residual=a.residual, blocked=dict(blocked))

    group.register(phase, handler)


RULEBOOKS = {"funding_plan": funding_plan, "asset_register": asset_register,
             "income_tax": income_tax, "waterfall": waterfall}
