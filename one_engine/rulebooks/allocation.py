"""Allocation rulebooks (module card 7): funding, the waterfall, a holder's distributions.

Each rulebook below states who is paid what, and in which order, through
:func:`one_engine.allocation.allocate`; the tests a waterfall applies are
Conditions, its coverage ratio is Measures. Moved from :mod:`one_engine.packs.pf` on 2026-10-14 (Ali chose to move the rulebooks three or more
archetypes declare out of the packs; ``one_engine/archetypes.yaml``).

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

``deficit_funding``
    The holders' support for a cash shortfall: at the end of its phase,
    in periods ``from`` (default 0) to ``until``, the named equity legs
    (one, or several with shares, as a funding residual) pay in what
    brings the cash back up to its floor, which the case must declare. Its
    handler runs after the phase's flows and after the rulebooks declared
    before it in the file, so it makes good what they leave. Averta fixed
    (step 8): the live HoldCo and DevCo run overdrafts nothing funds; here
    their shareholders cover them as they arise.

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
    ``flow_only`` (``from``, ``until``): in those periods only the period's
    own net cash flow is offered, never cash carried in. Averta's live S9
    (step 8) distributes current-period cash flow only, so a HoldCo
    overdraft (an unfloored cash account) is carried, not made good, until
    the first period after the window, when all the cash is offered.
    Cash a test traps inside the window is the exception: it is offered
    again, with the period's own flow, in the first period whose residual
    is not blocked (the live S9's distribution suspense, released when the
    lock-up clears; Averta's buyer, step 8).
    ``active`` (``from``, ``until``): the waterfall runs in those periods
    only; outside them nothing is paid and the cash is retained (Averta's
    live GP, step 8, sweeps its cash and settles its tax once, at the
    exit).

``capital_first``
    Divides the waterfall's residual for one holder (a ``sponsor_equity``
    contract): its unreturned capital is returned first, through the
    ``return_capital`` leg, and the rest is distributed. Averta's SPV
    (engine_v2 S10, step 8) splits each distribution this way, so its
    contributed capital is back to nil before profit is paid out.

Helpers ``_read_uses``, ``_use_amounts`` and :func:`sources_equal_uses` are
also read by ``pro_rata_funding`` (:mod:`one_engine.packs.pf`), which only
Bodmer's solar model uses.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Mapping

from one_engine.allocation import Tier, allocate
from one_engine.conditions import Test, evaluate
from one_engine.cuts import cash_flow_cut
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Posting
from one_engine.measures import coverage
# a term stated once or per period (``_at`` keeps the name the packs use for the same reading)
from one_engine.units import at as _at, exact


def _ref(group: Group, ref: str):
    cid, _, part = ref.partition(".")
    return group.contract(cid), part


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


# ── funding plan ──────────────────────────────────────────────────────

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


def deficit_funding(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    entity, cash, phase = r.take("entity"), r.take("cash"), r.take("phase")
    start = int(r.take_or_default("from", 0, reason="from the first period").value)
    until = int(r.take("until"))
    residual = _residual_legs(r.take("residual"), "deficit_funding.residual")
    if (entity, cash) not in group.floors:
        raise InputError(f"deficit_funding: {entity}.{cash} has no floor to fund up to")
    floor, name = exact(group.floors[(entity, cash)]), ctx["name"]

    def handler(g: Group, t: int) -> None:
        if not start <= t <= until:
            return
        gap = floor - g.book(entity).balance(cash, t)
        if gap > 0:
            _post_residual(g, residual, gap, t, phase)
            g.record(name, t, called=gap)

    group.register(phase, handler)


def sources_equal_uses(group: Group, entity: str, cash: str, phase: str) -> List[int]:
    """Periods whose funding phase leaves a net cash movement (sources differ from uses)."""
    net: Dict[int, Fraction] = {}
    for p in group.book(entity).postings:
        if p.phase == phase and cash in (p.debit, p.credit):
            net[p.period] = net.get(p.period, Fraction(0)) + (p.amount if p.debit == cash else -p.amount)
    return sorted(t for t, v in net.items() if v != 0)


# ── waterfall ─────────────────────────────────────────────────────────

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
    fo = r.take_or_default("flow_only", None, reason="all cash above the floor is offered").value
    flow_only = None
    if fo is not None:
        fr = Reader(fo, source="waterfall.flow_only")
        flow_only = (int(fr.take("from")), int(fr.take("until")))
        fr.finish()
    ac = r.take_or_default("active", None, reason="the waterfall runs every period").value
    active = None
    if ac is not None:
        ar = Reader(ac, source="waterfall.active")
        active = (int(ar.take("from")), int(ar.take("until")))
        ar.finish()
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

    # cash a test trapped inside the flow_only window: carried in, but offered again when the test clears
    pending = {"trapped": Fraction(0)}

    def handler(g: Group, t: int) -> None:
        if active is not None and not active[0] <= t <= active[1]:
            return                                  # outside its window the cash stays where it is
        book = g.book(entity)
        due = {name: book.balance(ref, t) for name, kind, ref, _ in tiers_spec if kind == "account"}
        flows = cash_flow_cut(book, cash, scheme, classes).values[t - book.start_period]
        cfads = flows - sum((due[n] for n in less), Fraction(0))
        if share != 1:
            # the part of the period's cash flow that falls while the debt is outstanding (a loan that
            # matures mid-year is covered by the cash flow of its days only)
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
        in_window = flow_only is not None and flow_only[0] <= t <= flow_only[1]
        if in_window:
            # only the period's own net cash flow is offered: cash carried in, a surplus or an overdraft,
            # is neither paid out nor made good (within the floor, when the cash has one); cash trapped
            # earlier in the window is the exception, offered again once the residual is not blocked
            own = book.balance(cash, t) - book.opening(cash, t)
            if residual not in blocked:
                own += pending["trapped"]
                pending["trapped"] = Fraction(0)
            available = max(Fraction(0), min(own, available) if (entity, cash) in g.floors else own)

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
            if in_window:
                pending["trapped"] += a.residual
        elif residual in ctx["distributors"]:
            ctx["distributors"][residual](g, t, a.residual, phase)
        else:
            c, leg = _ref(g, residual)
            g.post_leg(c, leg, a.residual, t, phase)
        g.record("waterfall", t, cfads=cfads, service=ds, dscr=dscr, available=available,
                 paid=dict(a.paid), residual=a.residual, blocked=dict(blocked))

    group.register(phase, handler)


# ── capital first (one holder's distributions) ────────────────────────

def capital_first(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    holder = r.take("holder")

    def distribute(g: Group, t: int, amount: Fraction, phase: str) -> None:
        # the unreturned capital is the capital account itself: each return of capital lowers it
        c = g.contract(holder)
        unreturned = g.book(c.parties["issuer"]).balance(c.acct("{id}.capital"), t)
        a = allocate(amount, [Tier("return_capital", unreturned)], residual="distribute")
        g.post_leg(c, "return_capital", a.paid["return_capital"], t, phase)
        g.post_leg(c, "distribute", a.residual, t, phase)

    ctx["distributors"][ctx["name"]] = distribute


RULEBOOKS = {"funding_plan": funding_plan, "deficit_funding": deficit_funding, "waterfall": waterfall,
             "capital_first": capital_first}
