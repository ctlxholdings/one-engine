"""Obligations pack: duties, prohibitions, claims and their ranks, guarantees, origination pools.

What this pack is for
---------------------
A deal is a set of commitments as much as a set of cash flows. A client
owes a fee *while* a collection route is permitted; an operator is
forbidden to route collections off the pledged account, and owes a
reparation if it does; a guarantor settles its lenders' put at par when the
servicer fails; claims crystallize, are paid in rank, and what the estate
cannot pay is extinguished, or called from the junior holder. The failure
panel (``cases/panel``, ``tests/test_case_esco_panel.py``) breaks standing
deals by flipping one of these commitments at a time. This pack holds the
mechanisms; the panel's deals and switches are data.

Every rulebook here posts inside **one book** (``entity``), between
accounts the case declares with their natures (``opening`` with nil values
is the usual place). Each leg moves its account by its nature, as
:func:`one_engine.rulebooks.postings.transfer` does: a debit raises a debit-natured
account (an asset, an expense) and lowers a credit-natured one (a
liability, equity, income), a credit the reverse. Amounts are exact; a
negative amount refuses, because the direction belongs to the accounts.

Facts and amounts
-----------------
A *fact* (a condition precedent, a forbidden act, a trigger) is ``true`` /
``false``, a number (non-zero is true: a formulas generator's ``t <
failure_period`` gives 1 or 0), or a list of those, one per period. An
*amount* is a number or a list of numbers, one per period. A ``{generator:
..., output: ...}`` reference stands for either (see :mod:`one_engine.cases`).

Rulebooks
---------
``duty``
    An obligation with a condition precedent. In each period of its window
    (``from``, ``until``; the whole grid by default) it is *operative* while
    ``condition`` holds and *suspended* otherwise. An operative duty
    performs: it posts ``perform`` (``debit``, ``credit``, ``amount``). A duty
    without ``perform`` is a pure determination: it posts nothing and only
    records its state. Each period's record (kind = the rulebook's name)
    carries ``status`` (``operative`` | ``suspended``), ``satisfied`` and the
    ``amount`` posted. A suspension is read from the condition each period;
    a stop with no cure is a condition that stays false. ``extinguish_on``
    (a fact, optional) ends the duty for good: from the first period it
    holds the duty is *extinguished*, posts nothing, and stays so even if
    the fact reads false again (a terminated agreement does not revive).

``prohibition``
    A forbidden act and its reparation. In each period where ``forbidden``
    holds the prohibition is *violated* and its ``remedy`` (``debit``,
    ``credit``, ``amount``) is posted: a diverted fee re-credited to the
    pledged account, or a fine paid out of the violator's pocket. The record
    carries ``violated`` and ``remedy_paid`` (the amount posted).

``claim``
    Claims recognised as liabilities: ``claims`` maps a name to ``{claim,
    against, ...}``; the claim account is credited and ``against`` debited.
    The amount is either stated (``amount``) or measured at fire time:
    ``share`` x ``index`` (default 1; a series is a regime path) x a reading
    of an account (``of: {account, read}``, ``read`` being ``flow``, the
    period's movement so far, or ``balance``, the balance so far). Timing is
    either a window (``from``, ``until``; the whole grid by default) or
    ``crystallizes_on``, a trigger: the claim is recognised once, in the
    first period the trigger holds. (Not ``on``: YAML reads that key as a
    boolean.) A claim is never negative. The record carries ``claims``
    (name -> amount recognised).

``put``
    A guarantee settled at par on a trigger: in the first period ``trigger``
    holds, the ``guarantor`` pays the ``claim``'s outstanding balance (par)
    into ``pool``. Once only. Ranking the pool over the creditors is the
    ``stack``'s job, so the sequence fail -> put -> par -> wipe is two
    rulebooks in file order. The record carries ``exercised`` and ``par``.

``stack``
    A priority stack over a pool: in each period of its window, after
    moving the whole balance of each ``sweep`` account into ``pool`` (an
    estate realised), the pool's balance is allocated
    (:func:`one_engine.allocation.allocate`) over ``tiers`` in order. Each tier
    is due its ``claim``'s balance. A tier's payment moves cash from the
    pool to its ``to`` account and discharges the claim against
    ``claims_book`` (the account claims were recognised against). A tier
    may instead be a *reserve*: ``{name, target, to}``, due what lifts the
    ``to`` account's balance to ``target`` (nothing once it is there),
    paid in rank like any tier and discharging nothing; a reserve topped
    before the claims is ranked first. What a claim tier is not paid
    follows its ``shortfall``:

    * ``carry``: it stays due (arrears);
    * ``extinguish``: it is written off unpaid into ``extinguished`` (the
      claimants' loss; the account is the stack's, required when a tier
      extinguishes);
    * ``call``: the tier ranks at par, paid in full whatever the pool. The
      gap is called from the ``residual`` holder before anything is paid.
      Ranks are sequential, so the call is everything due up to and
      including the last ``call`` tier, less the pool.

    What is left after the tiers goes to ``residual`` when the stack names
    one (a distribution to the junior holder), and otherwise stays in the
    pool: cash the claims do not reach is stranded there. The record
    carries ``available``, ``call``, ``paid``, ``extinguished`` and
    ``residual``.

``pool``
    An origination pool: ``members`` (id -> ``{quantity, performance,
    account}``) stream into ``pool_to`` for the ``terms`` of their stint from
    their ``entry`` period (``entries``). A member may have several stints,
    never overlapping: a lot that rolls over into a new cycle enters again,
    and each entry is gated on its own. Each period
    a member pays ``quantity`` x its ``performance`` (a number, or a series
    indexed by the period *of its stint*), credited to its own ``account``
    (its sub-ledger). ``thresholds`` gate entry: an ``origination_floor``
    reads its ``measure`` (a series) in the period *before* the entry, the
    last one observed when the cohort is originated, and blocks the entry
    when the reading is below ``floor``. ``if_missing`` (required) says what
    an entry with no earlier period to read does (``pass`` or ``fail``,
    :mod:`one_engine.conditions`). A blocked member never streams. The record
    carries ``entered``, ``skipped`` (member -> the thresholds it failed)
    and ``receipts``. The rulebook's check ``composition`` re-reads the
    books: in every period the members' sub-ledgers move by exactly what
    the pool posted into ``pool_to``, so a posting into a member's account
    from anywhere else turns it red.

``serviced_bullet``
    A non-recourse interest-only bullet serviced from cash (esco-product's
    ``interest_only`` type path; ACTUS PAM family). The interest is not
    computed here: a ``claim`` recognises each period's charge on ``claim``.
    In each period of the window, in good standing, the claim is paid *in
    full* out of ``cash`` to ``lender`` when the cash covers it, and
    nothing is paid otherwise: that miss is a payment default, and from
    then on every claim *capitalises* into ``debt`` whatever the cash. At
    ``maturity`` the whole cash balance is swept against claim plus debt
    (claim first, then principal); what it does not reach stays on ``debt``
    as the lenders' shortfall and any excess cash stays in ``cash``. A
    paid claim is discharged back through ``expense`` (where the claim was
    recognised), repaid principal back to ``funding`` (where the debt was
    recognised). The record carries ``paid``, ``capitalised``,
    ``defaulted`` and, at maturity, ``swept``. Why all or nothing: a
    coupon is a single payment; a partial payment is still a missed one,
    and the source (Indiana Toll Road, June 2014) is a missed payment,
    not a partial one.

``ranked_distribution``
    Fixed claims paid first, variable claims sharing what is left
    (esco-product's OP3 as its case converter writes it: direct assignee,
    pro rata). Each period of the window, each level's claim is what
    accrued on its ``claim`` account in the period; ``performance`` (a
    number or a series) x their total is collected from ``collect_from``
    into ``pool``. Every accrual is then discharged against
    ``claims_book``, whatever is paid against it, and the pool pays each
    level's ``to``. Fixed levels are paid in full when the cash covers
    them, and the variable levels share the remainder pro rata to their
    claims, each capped at its claim (what the caps leave stays in the
    pool). When the cash falls short of the fixed levels, ``shortfall``
    says who bears it:

    * ``absorb``: the fixed levels are paid in full anyway and the variable
      levels pay the deficit into the pool, pro rata, the same period;
    * ``none``: the fixed levels share the cash pro rata and bear the rest;
      the variable levels get nothing;
    * ``deferred``: as ``none``, and ``lag`` periods later each variable
      payee pays its pro rata share of the deficit to each fixed payee, pro
      rata to the fixed claims (a recovery due past the grid is not
      collected).

    The record carries ``available``, ``paid`` (claim account -> amount,
    negative when absorbed), ``deficit`` and ``recovered``.

Not here: index paths. A regime switch or an escalation ramp is a series,
written as data or as a formula of the ``formulas`` generator
(:mod:`one_engine.packs.tea`), and read by a claim's ``index`` or a
transfer's amount.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine.allocation import Tier, allocate
from one_engine.conditions import Test, evaluate
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Posting
from one_engine.units import exact

SOURCE = "obligations"


class ObligationError(ValueError):
    """A commitment met a value it cannot carry while the books run (a negative claim, an overdrawn pool)."""


# ── shared helpers ───────────────────────────────────────────────────────────

def _at(x: Any, t: int, where: str) -> Fraction:
    """An amount that is a number or a per-period series, at period ``t``."""
    if isinstance(x, (list, tuple)):
        if t >= len(x):
            raise InputError(f"{where}: a series of {len(x)} values has no period {t}")
        return exact(x[t])
    return exact(x)


def _fact(x: Any, t: int, where: str) -> bool:
    """A fact (bool, number, or a per-period series of those) at period ``t``; non-zero is true."""
    if isinstance(x, (list, tuple)):
        if t >= len(x):
            raise InputError(f"{where}: a series of {len(x)} facts has no period {t}")
        x = x[t]
    if isinstance(x, bool):
        return x
    return exact(x) != 0


def _window(r: Reader, periods: int, where: str) -> Tuple[int, int]:
    first = int(r.take_or_default("from", 0, reason="from the first period").value)
    last = int(r.take_or_default("until", periods - 1, reason="to the last period").value)
    if not 0 <= first <= last < periods:
        raise InputError(f"{where}: window {first}..{last} is not inside 0..{periods - 1}")
    return first, last


def _move(g: Group, entity: str, t: int, phase: str, debit: str, credit: str, amount: Fraction,
          event: str) -> None:
    """Post ``amount`` (positive) from ``credit`` to ``debit``, each leg moving its account by its nature."""
    if amount < 0:
        raise ObligationError(f"{event}: negative amount {amount}; the direction belongs to the accounts")
    if amount == 0:
        return
    book = g.book(entity)
    # a debit raises a debit-natured account and lowers a credit-natured one; a credit the reverse
    eff = {a: (1 if book.accounts[a].nature.debit_natured == (a == debit) else -1) for a in (debit, credit)}
    g.post(entity, Posting(t, phase, debit, credit, amount, eff, SOURCE, event))


def _posting(raw: Mapping[str, Any], where: str) -> Tuple[str, str, Any]:
    """A ``{debit, credit, amount}`` block."""
    r = Reader(raw, source=where)
    out = (r.take("debit"), r.take("credit"), r.take("amount"))
    r.finish()
    return out


# ── duty ─────────────────────────────────────────────────────────────────────

def duty(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """An obligation with a condition precedent; see the module docstring."""
    name, where = ctx["name"], f"duty {ctx['name']}"
    entity, phase, condition = r.take("entity"), r.take("phase"), r.take("condition")
    first, last = _window(r, group.periods, where)
    raw = r.take_or_default("perform", None, reason="a pure determination: the duty records its state only").value
    perform = None if raw is None else _posting(raw, f"{where}.perform")
    extinguish_on = r.take_or_default("extinguish_on", False, reason="nothing ends the duty for good").value
    state = {"extinguished": False}

    def handler(g: Group, t: int) -> None:
        if not first <= t <= last:
            return
        # termination is terminal: once the fact holds the duty is gone, whatever the fact reads later
        state["extinguished"] = state["extinguished"] or _fact(extinguish_on, t, f"{where}.extinguish_on")
        operative = not state["extinguished"] and _fact(condition, t, f"{where}.condition")
        x = Fraction(0)
        if operative and perform is not None:
            debit, credit, amount = perform
            x = _at(amount, t, f"{where}.perform.amount")
            _move(g, entity, t, phase, debit, credit, x, f"{name}.perform:{t}")
        status = "extinguished" if state["extinguished"] else "operative" if operative else "suspended"
        g.record(name, t, status=status, satisfied=operative, amount=x)

    group.register(phase, handler)


# ── prohibition ──────────────────────────────────────────────────────────────

def prohibition(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A forbidden act and its reparation; see the module docstring."""
    name, where = ctx["name"], f"prohibition {ctx['name']}"
    entity, phase, forbidden = r.take("entity"), r.take("phase"), r.take("forbidden")
    debit, credit, amount = _posting(r.take("remedy"), f"{where}.remedy")

    def handler(g: Group, t: int) -> None:
        violated = _fact(forbidden, t, f"{where}.forbidden")
        x = _at(amount, t, f"{where}.remedy.amount") if violated else Fraction(0)
        _move(g, entity, t, phase, debit, credit, x, f"{name}.remedy:{t}")
        g.record(name, t, violated=violated, remedy_paid=x)

    group.register(phase, handler)


# ── claim ────────────────────────────────────────────────────────────────────

def claim(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Claims recognised as liabilities, stated or measured at fire time; see the module docstring."""
    name, entity, phase = ctx["name"], r.take("entity"), r.take("phase")
    specs = []
    for cname, raw in r.take("claims").items():
        where = f"claim {name}.{cname}"
        cr = Reader(raw, source=where)
        acct, against = cr.take("claim"), cr.take("against")
        stated = "amount" in raw
        if stated == ("share" in raw or "of" in raw):
            raise InputError(f"{where}: give either amount, or share and of")
        if stated:
            measure = ("amount", cr.take("amount"))
        else:
            orr = Reader(cr.take("of"), source=f"{where}.of")
            account, read = orr.take("account"), orr.take("read")
            orr.finish()
            if read not in ("flow", "balance"):
                raise InputError(f"{where}.of: read must be flow or balance, not {read!r}")
            index = cr.take_or_default("index", 1, reason="no index moves the share").value
            measure = ("share", (exact(cr.take("share")), index, account, read))
        if "crystallizes_on" in raw and ("from" in raw or "until" in raw):
            raise InputError(f"{where}: give either a trigger (crystallizes_on) or a window (from, until)")
        trigger = cr.take("crystallizes_on") if "crystallizes_on" in raw else None
        window = None if trigger is not None else _window(cr, group.periods, where)
        cr.finish()
        specs.append((cname, acct, against, measure, trigger, window, where))
    crystallized: Dict[str, int] = {}

    def amount_at(g: Group, t: int, measure, where: str) -> Fraction:
        kind, m = measure
        if kind == "amount":
            return _at(m, t, f"{where}.amount")
        share, index, account, read = m
        book = g.book(entity)
        reading = book.balance(account, t) - (book.opening(account, t) if read == "flow" else 0)
        return share * _at(index, t, f"{where}.index") * reading

    def handler(g: Group, t: int) -> None:
        recognised = {}
        for cname, acct, against, measure, trigger, window, where in specs:
            if trigger is not None:
                # a crystallizing claim is recognised once, in the first period its trigger holds
                if cname in crystallized or not _fact(trigger, t, f"{where}.crystallizes_on"):
                    continue
                crystallized[cname] = t
            elif not window[0] <= t <= window[1]:
                continue
            x = amount_at(g, t, measure, where)
            # a negative claim refuses in _move: the direction belongs to the accounts
            _move(g, entity, t, phase, against, acct, x, f"{name}.{cname}:{t}")
            recognised[cname] = x
        g.record(name, t, claims=recognised)

    group.register(phase, handler)


# ── put ──────────────────────────────────────────────────────────────────────

def put(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A guarantee settled at par on a trigger; see the module docstring."""
    name, where = ctx["name"], f"put {ctx['name']}"
    entity, phase, trigger = r.take("entity"), r.take("phase"), r.take("trigger")
    guarantor, pool, acct = r.take("guarantor"), r.take("pool"), r.take("claim")
    state: Dict[str, Optional[int]] = {"exercised": None}

    def handler(g: Group, t: int) -> None:
        if state["exercised"] is not None or not _fact(trigger, t, f"{where}.trigger"):
            return
        state["exercised"] = t
        par = g.book(entity).balance(acct, t)          # par is what the guaranteed claim stands at now
        _move(g, entity, t, phase, pool, guarantor, par, f"{name}.par:{t}")
        g.record(name, t, exercised=True, par=par)

    group.register(phase, handler)


# ── stack ────────────────────────────────────────────────────────────────────

_SHORTFALL = ("carry", "extinguish", "call")


def stack(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A priority stack over a pool; see the module docstring."""
    name, where = ctx["name"], f"stack {ctx['name']}"
    entity, phase, pool, book_acct = r.take("entity"), r.take("phase"), r.take("pool"), r.take("claims_book")
    first, last = _window(r, group.periods, where)
    sweep = list(r.take_or_default("sweep", [], reason="nothing is swept into the pool").value)
    residual = r.take_or_default("residual", None, reason="what the tiers leave stays in the pool").value
    extinguished = r.take_or_default("extinguished", None, reason="no tier extinguishes").value
    tiers = []
    for i, raw in enumerate(r.take("tiers")):
        tr = Reader(raw, source=f"{where}.tiers[{i}]")
        if "target" in raw:
            # a reserve tier: due what lifts its account to target; no claim, nothing carried
            tier = (tr.take("name"), None, tr.take("to"), "reserve", exact(tr.take("target")))
            tr.finish()
            if tier[4] < 0:
                raise InputError(f"{where}.tiers[{i}]: a reserve target is never negative")
            tiers.append(tier)
            continue
        tier = (tr.take("name"), tr.take("claim"), tr.take("to"), tr.take("shortfall"), None)
        tr.finish()
        if tier[3] not in _SHORTFALL:
            raise InputError(f"{where}.tiers[{i}]: shortfall must be one of {_SHORTFALL}, not {tier[3]!r}")
        tiers.append(tier)
    names = [x[0] for x in tiers]
    if len(set(names)) != len(names):
        raise InputError(f"{where}: a tier is named twice: {names}")
    calls = [i for i, x in enumerate(tiers) if x[3] == "call"]
    if calls and residual is None:
        raise InputError(f"{where}: a tier paid at par calls its gap from the residual holder; name one (residual)")
    if any(x[3] == "extinguish" for x in tiers) and extinguished is None:
        raise InputError(f"{where}: a tier extinguishes what it is not paid; name the account (extinguished)")

    def handler(g: Group, t: int) -> None:
        if not first <= t <= last:
            return
        book = g.book(entity)
        for src in sweep:
            x = book.balance(src, t)
            # the whole balance moves, whichever way it stands
            _move(g, entity, t, phase, *((pool, src) if x > 0 else (src, pool)), abs(x), f"{name}.sweep.{src}:{t}")
        available = book.balance(pool, t)
        if available < 0:
            raise ObligationError(f"{where}, period {t}: the pool {pool} is overdrawn ({available}) before the stack")
        due = {n: (max(Fraction(0), target - book.balance(to, t)) if c is None else book.balance(c, t))
               for n, c, to, _, target in tiers}
        for n, x in due.items():
            if x < 0:
                raise ObligationError(f"{where}, period {t}: tier {n} is due a negative {x}")
        # ranks are sequential: paying the last par tier in full pays every tier ahead of it first
        call = Fraction(0)
        if calls:
            call = max(Fraction(0), sum((due[n] for n in names[:calls[-1] + 1]), Fraction(0)) - available)
        a = allocate(available + call, [Tier(n, due[n]) for n in names], residual="residual")
        _move(g, entity, t, phase, pool, residual or pool, call, f"{name}.call:{t}")
        gone = {}
        for n, c, to, shortfall, _ in tiers:
            paid = a.paid[n]
            _move(g, entity, t, phase, to, pool, paid, f"{name}.{n}.paid:{t}")
            if c is None:
                continue                     # a reserve tier discharges no claim
            _move(g, entity, t, phase, c, book_acct, paid, f"{name}.{n}.discharged:{t}")
            unpaid = due[n] - paid
            if unpaid and shortfall == "extinguish":
                _move(g, entity, t, phase, c, extinguished, unpaid, f"{name}.{n}.extinguished:{t}")
                gone[n] = unpaid
        if residual is not None:
            _move(g, entity, t, phase, residual, pool, a.residual, f"{name}.residual:{t}")
        g.record(name, t, available=available, call=call, paid=dict(a.paid), extinguished=gone,
                 residual=a.residual)

    group.register(phase, handler)


# ── pool ─────────────────────────────────────────────────────────────────────

def pool(group: Group, r: Reader, ctx: Mapping[str, Any]):
    """An origination pool with entry floors; see the module docstring."""
    name, where = ctx["name"], f"pool {ctx['name']}"
    entity, phase, pool_to = r.take("entity"), r.take("phase"), r.take("pool_to")
    members = {}
    for mid, raw in r.take("members").items():
        mr = Reader(raw, source=f"{where}.members.{mid}")
        members[mid] = (exact(mr.take("quantity")),
                        mr.take_or_default("performance", 1, reason="the member performs as expected").value,
                        mr.take("account"))
        mr.finish()
    entries = []
    for i, raw in enumerate(r.take("entries")):
        er = Reader(raw, source=f"{where}.entries[{i}]")
        entry = (er.take("member"), int(er.take("entry")), int(er.take("terms")))
        er.finish()
        if entry[0] not in members:
            raise InputError(f"{where}.entries[{i}]: unknown member '{entry[0]}'")
        for mid, at, terms in entries:
            # a member may roll over into a new stint, but is never in two at once
            if mid == entry[0] and at < entry[1] + entry[2] and entry[1] < at + terms:
                raise InputError(f"{where}: member '{mid}' has overlapping stints "
                                 f"{at}..{at + terms - 1} and {entry[1]}..{entry[1] + entry[2] - 1}")
        entries.append(entry)
    thresholds = []
    for i, raw in enumerate(r.take("thresholds")):
        tr = Reader(raw, source=f"{where}.thresholds[{i}]")
        tid, kind = tr.take("id"), tr.take("kind")
        if kind != "origination_floor":
            raise InputError(f"{where}.thresholds[{i}]: kind must be origination_floor, not {kind!r}")
        if_missing = tr.take("if_missing")
        if if_missing not in ("pass", "fail"):
            raise InputError(f"{where}.thresholds[{i}]: an entry is taken or not: if_missing is pass or fail")
        thresholds.append((Test(tid, ">=", exact(tr.take("floor")), if_missing), tr.take("measure")))
        tr.finish()
    active: Dict[str, List[Tuple[int, int]]] = {}

    def handler(g: Group, t: int) -> None:
        entered: List[str] = []
        skipped: Dict[str, List[str]] = {}
        for mid, at, terms in entries:
            if at != t:
                continue
            # the floor reads the period before entry: the last one observed when the cohort is originated
            failed = [test.name for test, measure in thresholds
                      if evaluate(test, None if t == 0 else _at(measure, t - 1, f"{where}.{test.name}")).verdict
                      == "fail"]
            if failed:
                skipped[mid] = failed
            else:
                active.setdefault(mid, []).append((at, terms))
                entered.append(mid)
        receipts = {}
        for mid, stints in active.items():
            live = [at for at, terms in stints if at <= t < at + terms]
            if not live:
                continue
            at = live[0]                     # stints never overlap: at most one is live
            quantity, performance, acct = members[mid]
            x = quantity * _at(performance, t - at, f"{where}.members.{mid}.performance")
            _move(g, entity, t, phase, pool_to, acct, x, f"{name}.{mid}:{t}")
            if x:
                receipts[mid] = x
        g.record(name, t, entered=entered, skipped=skipped, receipts=receipts)

    def composition() -> List[str]:
        """Every period, the members' sub-ledgers moved by exactly what the pool posted into ``pool_to``."""
        book, fails = group.book(entity), []
        accounts = {m[2] for m in members.values()}
        for t in range(book.start_period, book.periods):
            moved = Fraction(0)
            for a in accounts:
                # a member's sub-ledger is credited by the pool: read the movement on the credit side
                sign = -1 if book.accounts[a].nature.debit_natured else 1
                moved += sign * (book.balance(a, t) - book.opening(a, t))
            pooled = sum((p.amount for p in book.postings
                          if p.period == t and p.debit == pool_to and p.event.startswith(f"{name}.")), Fraction(0))
            if moved != pooled:
                fails.append(f"period {t}: members moved {float(moved):,.2f}, the pool received {float(pooled):,.2f}")
        return fails

    group.register(phase, handler)
    return {"composition": composition}


# ── serviced bullet ──────────────────────────────────────────────────────────

def serviced_bullet(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """An interest-only bullet paid all or nothing, capitalising after a default; see the module docstring."""
    name, where = ctx["name"], f"serviced_bullet {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    cash, lender, debt, acct = r.take("cash"), r.take("lender"), r.take("debt"), r.take("claim")
    expense, funding = r.take("expense"), r.take("funding")
    first, last = _window(r, group.periods, where)
    maturity = int(r.take("maturity"))
    if not first <= maturity <= last:
        raise InputError(f"{where}: maturity {maturity} is not inside the window {first}..{last}")
    state = {"defaulted": False}

    def handler(g: Group, t: int) -> None:
        if not first <= t <= maturity:
            return
        book = g.book(entity)
        due = book.balance(acct, t)
        if t == maturity:
            # the estate is swept: the claim is discharged first, then principal
            pay = max(Fraction(0), min(book.balance(cash, t), book.balance(debt, t) + due))
            on_claim = min(pay, due)
            _move(g, entity, t, phase, lender, cash, pay, f"{name}.bullet:{t}")
            _move(g, entity, t, phase, acct, expense, on_claim, f"{name}.bullet.claim:{t}")
            _move(g, entity, t, phase, debt, funding, pay - on_claim, f"{name}.bullet.debt:{t}")
            g.record(name, t, swept=pay, defaulted=state["defaulted"])
            return
        if due == 0:
            g.record(name, t, paid=Fraction(0), capitalised=Fraction(0), defaulted=state["defaulted"])
            return
        if not state["defaulted"] and book.balance(cash, t) >= due:
            _move(g, entity, t, phase, lender, cash, due, f"{name}.interest:{t}")
            _move(g, entity, t, phase, acct, expense, due, f"{name}.interest.claim:{t}")
            g.record(name, t, paid=due, capitalised=Fraction(0), defaulted=False)
            return
        # a claim the cash cannot cover is a payment default; it and every later claim capitalise
        state["defaulted"] = True
        _move(g, entity, t, phase, acct, debt, due, f"{name}.capitalise:{t}")
        g.record(name, t, paid=Fraction(0), capitalised=due, defaulted=True)

    group.register(phase, handler)


# ── ranked distribution ──────────────────────────────────────────────────────

_ABSORPTION = ("absorb", "none", "deferred")


def ranked_distribution(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Fixed claims paid first, variable claims sharing the rest, a shortfall absorbed or recovered; see the module docstring."""
    name, where = ctx["name"], f"ranked_distribution {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    source, pool, book_acct = r.take("collect_from"), r.take("pool"), r.take("claims_book")
    first, last = _window(r, group.periods, where)
    performance = r.take_or_default("performance", 1, reason="the claims are collected in full").value
    shortfall = r.take("shortfall")
    if shortfall not in _ABSORPTION:
        raise InputError(f"{where}: shortfall must be one of {_ABSORPTION}, not {shortfall!r}")
    lag = int(r.take_or_default("lag", 0, reason="no recovery is deferred").value)
    if (shortfall == "deferred") != (lag > 0):
        raise InputError(f"{where}: a deferred recovery waits at least one period (lag), and only it has a lag")
    levels = []
    for i, raw in enumerate(r.take("levels")):
        lr = Reader(raw, source=f"{where}.levels[{i}]")
        level = (lr.take("claim"), lr.take("to"), lr.take("kind"))
        lr.finish()
        if level[2] not in ("fixed", "variable"):
            raise InputError(f"{where}.levels[{i}]: kind must be fixed or variable, not {level[2]!r}")
        levels.append(level)
    claims = [x[0] for x in levels]
    if len(set(claims)) != len(claims):
        raise InputError(f"{where}: a claim account ranks twice: {claims}")
    # recoveries owed: period -> [(variable payee, fixed payee, amount)]
    pending: Dict[int, List[Tuple[str, str, Fraction]]] = {}

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        paid: Dict[str, Fraction] = {}
        available = deficit = Fraction(0)
        if first <= t <= last:
            # each level's claim is what accrued on its account this period
            due = {c: book.balance(c, t) - book.opening(c, t) for c in claims}
            total = sum(due.values(), Fraction(0))
            if total:
                available = _at(performance, t, f"{where}.performance") * total
                _move(g, entity, t, phase, pool, source, available, f"{name}.collect:{t}")
                fixed = [c for c, _, k in levels if k == "fixed"]
                variable = [c for c, _, k in levels if k == "variable"]
                fixed_total = sum((due[c] for c in fixed), Fraction(0))
                var_total = sum((due[c] for c in variable), Fraction(0))
                remainder = available - fixed_total
                if remainder >= 0 or shortfall == "absorb":
                    paid = {c: due[c] for c in fixed}
                    for c in variable:
                        # a deficit is shared pro rata to the claims; a surplus too, capped at each claim
                        share = remainder * due[c] / var_total if var_total else Fraction(0)
                        paid[c] = share if remainder < 0 else min(due[c], share)
                else:
                    # the fixed levels share the cash; the variable levels get nothing now
                    paid = {c: available * due[c] / fixed_total for c in fixed}
                    paid.update({c: Fraction(0) for c in variable})
                    deficit = -remainder
                    if shortfall == "deferred" and var_total:
                        to = {c: x for c, x, _ in levels}
                        for v in variable:
                            for f in fixed:
                                pending.setdefault(t + lag, []).append(
                                    (to[v], to[f], deficit * due[v] / var_total * due[f] / fixed_total))
                for c, to_acct, _ in levels:
                    # every accrual is extinguished, whatever is paid against it
                    _move(g, entity, t, phase, c, book_acct, due[c], f"{name}.{c}.discharged:{t}")
                    x = paid[c]
                    if x >= 0:
                        _move(g, entity, t, phase, to_acct, pool, x, f"{name}.{c}.paid:{t}")
                    else:
                        # an absorbed deficit: the variable payee pays its share into the pool
                        _move(g, entity, t, phase, pool, to_acct, -x, f"{name}.{c}.absorbed:{t}")
        recovered = Fraction(0)
        for payer, payee, x in pending.pop(t, []):
            _move(g, entity, t, phase, payee, payer, x, f"{name}.recover:{t}")
            recovered += x
        g.record(name, t, available=available, paid=paid, deficit=deficit, recovered=recovered)

    group.register(phase, handler)


RULEBOOKS = {"duty": duty, "prohibition": prohibition, "claim": claim, "put": put, "stack": stack, "pool": pool,
             "serviced_bullet": serviced_bullet, "ranked_distribution": ranked_distribution}
