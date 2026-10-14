"""Posting rulebooks (module card 4, the ledger kernel): amounts the case states, posted as stated or as due.

None of these decides an amount: the case states it (a schedule, a target,
a claim that falls due) and the rulebook posts it, so what they add is
every balance left behind and the cash moved, read back as any other
account. ``transfer``, ``held_balance`` and ``settle`` moved from
:mod:`one_engine.packs.tea`, ``pay_as_due`` from :mod:`one_engine.packs.pf`,
on 2026-10-14 (Ali chose to move the rulebooks three or more archetypes
declare out of the packs; ``one_engine/archetypes.yaml``).

Rulebooks
---------
``pay_as_due``
    The listed claims paid in full when due, whatever the cash: for a case
    that lets cash go negative to show a shortfall (ModelOff 2013 "Hard
    Times", step 7l). The overdraft that implies is not modelled, so cash
    is left unfloored; a waterfall would pay only from cash above the floor.

``held_balance``, ``settle``, ``transfer``
    See each function's docstring.
"""
from __future__ import annotations

from typing import Any, Mapping

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
# a term stated once or per period (``_at`` keeps the name the packs use for the same reading)
from one_engine.units import at as _at


def held_balance(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Working-capital accounts held at a target; the change moves cash.

    Case keys: ``entity``, ``cash``, ``phase``, and ``accounts`` (name ->
    ``{side: asset | liability, target}``, the target a number or a series,
    and optionally ``against``: the account the change is posted against
    instead of cash). A balance carried in on the opening balance sheet is
    the starting point: the first period moves only the difference to its
    target.

    ``against`` an expense is an inventory bought as an expense: purchases
    are expensed (and paid) by a cost contract, and the stock held at the
    period end is taken back out of the expense, so the expense is the cost
    of what was used (opening stock plus purchases less closing stock).
    """
    entity, cash, phase = r.take("entity"), r.take("cash"), r.take("phase")
    held = []
    for acct, raw in r.take("accounts").items():
        ar = Reader(raw, source=f"held_balance.accounts.{acct}")
        side, target = ar.take("side"), ar.take("target")
        against = ar.take_or_default("against", cash, reason="the change moves cash").value
        ar.finish()
        if side not in ("asset", "liability"):
            raise InputError(f"held_balance.accounts.{acct}: side must be asset or liability, not {side!r}")
        group.declare(entity, acct, Nature.ASSET if side == "asset" else Nature.LIABILITY, ctx["currency"])
        held.append((acct, side, target, against))

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for acct, side, target, against in held:
            change = _at(target, t) - book.balance(acct, t)
            if not change:
                continue
            # raising an asset debits it; raising a liability credits it; the other leg takes the other
            # side. Each leg's effect follows its nature: a debit raises a debit-natured account (cash,
            # an expense) and lowers a credit-natured one, and a credit the reverse
            up = (change > 0) == (side == "asset")
            debit, credit = (acct, against) if up else (against, acct)
            eff = {a: (1 if book.accounts[a].nature.debit_natured == (a == debit) else -1) for a in (debit, credit)}
            g.post(entity, Posting(t, phase, debit, credit, abs(change), eff, "operating", f"{acct}.held:{t}"))

    group.register(phase, handler)


def settle(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Pay each listed payable in full at the end of ``phase``, every period.

    Case keys: ``entity``, ``cash``, ``phase``, ``accounts`` (liability
    accounts another rulebook or contract declares and credits). A payable
    below nil (a tax credit, ``income_tax`` with ``loss_relief: refund``)
    is a refund due, and is received the same way.
    """
    entity, cash, phase, accounts = r.take("entity"), r.take("cash"), r.take("phase"), list(r.take("accounts"))

    def handler(g: Group, t: int) -> None:
        for acct in accounts:
            x = g.book(entity).balance(acct, t)
            if x > 0:
                g.post(entity, Posting(t, phase, acct, cash, x, {acct: -1, cash: -1}, "operating", f"{acct}.settle:{t}"))
            elif x < 0:
                g.post(entity, Posting(t, phase, cash, acct, -x, {cash: 1, acct: 1}, "operating", f"{acct}.settle:{t}"))

    group.register(phase, handler)


def transfer(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Stated amounts posted between accounts each period.

    Case keys: ``entity``, ``phase``, ``declare`` (optional: account ->
    nature, for accounts no other piece declares), and ``moves`` (name ->
    ``{debit, credit, amount}``, the amount a number or a series, posted in
    the order given). Each leg moves its account by its nature: a debit
    raises a debit-natured account (cash, an asset, an expense) and lowers
    a credit-natured one, a credit the reverse. A negative amount is
    posted the other way round.

    The amounts are the business's own schedule (formulas over the gold's
    inputs); what the books add is every balance they leave behind, and the
    cash they move, read back as any other account.
    """
    entity, phase = r.take("entity"), r.take("phase")
    for acct, nature in (r.take_or_default("declare", {}, reason="the accounts are declared elsewhere").value or {}).items():
        try:
            group.declare(entity, acct, Nature(nature), ctx["currency"])
        except ValueError as e:
            raise InputError(f"transfer.declare.{acct}: {nature!r} is not a nature") from e
    moves = []
    for name, raw in r.take("moves").items():
        mr = Reader(raw, source=f"transfer.moves.{name}")
        moves.append((name, mr.take("debit"), mr.take("credit"), mr.take("amount")))
        mr.finish()

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for name, debit, credit, amount in moves:
            x = _at(amount, t)
            if not x:
                continue
            dr, cr = (debit, credit) if x > 0 else (credit, debit)
            eff = {a: (1 if book.accounts[a].nature.debit_natured == (a == dr) else -1) for a in (dr, cr)}
            g.post(entity, Posting(t, phase, dr, cr, abs(x), eff, "operating", f"{ctx['name']}.{name}:{t}"))

    group.register(phase, handler)


# ── pay as due ────────────────────────────────────────────────────────

def pay_as_due(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    phase, refs = r.take("phase"), list(r.take("claims"))

    def handler(g: Group, t: int) -> None:
        claims = g.claims(t)
        for ref in refs:
            if ref in claims and claims[ref].due:
                g.settle(claims[ref], claims[ref].due, phase)

    group.register(phase, handler)


RULEBOOKS = {"held_balance": held_balance, "settle": settle, "transfer": transfer, "pay_as_due": pay_as_due}
