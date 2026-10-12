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

* the generator ``formulas``: named values computed from the gold's inputs,
  so a translation never retypes a number. A gold input is referenced by
  its name; anything derived from it is a formula over those names;
* the rulebook ``held_balance``: a working-capital account (an inventory, a
  deposit) held at a target each period, the change moving cash;
* the rulebook ``settle``: payables paid in full in the period they arise
  (a small business's tax paid within its year);
* the rulebook ``transfer``: a stated amount posted from one account to
  another each period (a payment calendar the business states: a public
  works contract billed, settled and its retention released on its own
  timetable);
* the rulebook ``write_down``: a stated amount of an asset written off to
  an expense each period (grain lost in store);
* the rulebook ``asset_pool``: a pool of like assets in steady state,
  depreciated at its gross cost over its life each period, the vintage that
  retires replaced by the period's spend.

Formulas
--------
Exact arithmetic over names: ``+ - * /``, ``%`` (the remainder: an event
every so many periods), ``**`` to a whole power (a decline compounding by
period), unary minus, numbers, the period
index ``t``, the comparisons ``< <= > >= == !=`` (1 when true, else 0), the
conditional ``a if c else b``, ``min``, ``max``, ``abs``, and ``lag(name, k)``:
the value ``name`` had ``k`` periods earlier, nil before the first period
(nothing had happened yet: a case that starts a business from nothing, then
warms up to its steady state, reads its payment calendar this way), and
``cumsum(name)``: the running total of ``name`` from the first period to
this one (containers landed to date; with ``lag``, those landed in the last
so many periods). A number typed
in a formula is read from its text (``0.3`` is 3/10). A formula that reads
``t`` or a series is a series of ``periods`` values; otherwise it is a
number. ``{each_period: formula}`` is a series even when every period is
the same (a spend schedule a contract reads period by period). A name that
is neither an input nor an earlier value refuses.
"""
from __future__ import annotations

import ast
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Union

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.units import exact

Value = Union[Fraction, List[Fraction]]

_FUNCS = {"min": min, "max": max, "abs": abs}
_CMP = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
        ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}


def _number(v: Any, where: str) -> Fraction:
    """An input value as an exact number: a YAML float is read from its shortest text (0.3 is 3/10)."""
    if isinstance(v, bool):
        raise InputError(f"{where}: {v!r} is not a number")
    if isinstance(v, float):
        return exact(repr(v))
    try:
        return exact(v)
    except (TypeError, ValueError) as e:
        raise InputError(f"{where}: {v!r} is not a number") from e


class _Formula:
    """One formula, parsed once and evaluated at a period; see the module docstring."""

    def __init__(self, text: Any, where: str):
        self.where, self.text = where, str(text)
        if not isinstance(text, str):
            self.tree = ast.Constant(_number(text, where))
            return
        try:
            self.tree = ast.parse(text, mode="eval").body
        except SyntaxError as e:
            raise InputError(f"{where}: cannot parse formula {text!r}") from e

    def names(self) -> set:
        return {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)} - set(_FUNCS) - {"lag", "cumsum"}

    def lags(self) -> bool:
        """Whether the formula reads other periods (``lag``, ``cumsum``): it is then a series."""
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("lag", "cumsum")
                   for n in ast.walk(self.tree))

    def at(self, t: int, ns: Mapping[str, Value]) -> Fraction:
        return self._eval(self.tree, t, ns)

    def _eval(self, n: ast.AST, t: int, ns: Mapping[str, Value]) -> Fraction:
        ev = lambda x: self._eval(x, t, ns)
        if isinstance(n, ast.BinOp) and type(n.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod):
            a, b = ev(n.left), ev(n.right)
            if isinstance(n.op, (ast.Div, ast.Mod)):
                if b == 0:
                    raise InputError(f"{self.where}: division by zero in {self.text!r} at period {t}")
                return a / b if isinstance(n.op, ast.Div) else a % b
            return a + b if isinstance(n.op, ast.Add) else a - b if isinstance(n.op, ast.Sub) else a * b
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            a, b = ev(n.left), ev(n.right)
            if b.denominator != 1 or (a == 0 and b < 0):
                raise InputError(f"{self.where}: {a} ** {b} is not exact ({self.text!r} at period {t})")
            return a ** int(b)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = ev(n.operand)
            return -v if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            return Fraction(int(_CMP[type(n.ops[0])](ev(n.left), ev(n.comparators[0]))))
        if isinstance(n, ast.IfExp):
            return ev(n.body) if ev(n.test) else ev(n.orelse)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "lag" and not n.keywords:
            if len(n.args) != 2 or not isinstance(n.args[0], ast.Name) or n.args[0].id not in ns:
                raise InputError(f"{self.where}: lag takes an earlier value's name and a number of periods ({self.text!r})")
            k = ev(n.args[1])
            if k.denominator != 1 or k < 0:
                raise InputError(f"{self.where}: lag by {k} periods is not a whole number of periods ({self.text!r})")
            back = t - int(k)
            v = ns[n.args[0].id]
            return Fraction(0) if back < 0 else (v[back] if isinstance(v, list) else v)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "cumsum" and not n.keywords:
            if len(n.args) != 1 or not isinstance(n.args[0], ast.Name) or n.args[0].id not in ns:
                raise InputError(f"{self.where}: cumsum takes an earlier value's name ({self.text!r})")
            v = ns[n.args[0].id]
            # a number is the same every period: its running total is that many of it
            return sum(v[:t + 1], Fraction(0)) if isinstance(v, list) else v * (t + 1)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            return Fraction(_FUNCS[n.func.id](*[ev(a) for a in n.args]))
        if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and isinstance(n.value, (int, float, Fraction)):
            if isinstance(n.value, Fraction):
                return n.value
            return exact(ast.get_source_segment(self.text, n) or repr(n.value))
        if isinstance(n, ast.Name):
            if n.id == "t":
                return Fraction(t)
            if n.id not in ns:
                raise InputError(f"{self.where}: '{n.id}' is neither an input nor an earlier value ({self.text!r})")
            v = ns[n.id]
            return v[t] if isinstance(v, list) else v
        raise InputError(f"{self.where}: {type(n).__name__} is not allowed in a formula ({self.text!r})")


def formulas(r: Reader) -> Dict[str, Value]:
    """Named values from named inputs (a generator); see the module docstring.

    Case keys: ``periods``; ``inputs`` (name -> number, or a list of numbers: the gold's inputs,
    filled in by the oracle family from the vendored gold); ``values``
    (name -> formula, in order, each reading inputs and earlier values).
    Outputs: every input, and every value.
    """
    periods = int(r.take("periods"))
    # an input is a number, or a list of numbers (a series the source prints period by period)
    ns: Dict[str, Value] = {k: ([_number(x, f"formulas.inputs.{k}[{i}]") for i, x in enumerate(v)]
                                if isinstance(v, list) else _number(v, f"formulas.inputs.{k}"))
                            for k, v in r.take("inputs").items()}
    for name, text in r.take("values").items():
        if name in ns:
            raise InputError(f"formulas.values.{name}: the name is already an input or an earlier value")
        each = isinstance(text, Mapping)
        if each:
            if set(text) != {"each_period"}:
                raise InputError(f"formulas.values.{name}: a mapping value takes only each_period")
            text = text["each_period"]
        f = _Formula(text, f"formulas.values.{name}")
        series = each or f.lags() or "t" in f.names() or any(isinstance(ns.get(x), list) for x in f.names())
        ns[name] = [f.at(t, ns) for t in range(periods)] if series else f.at(0, ns)
    return ns


GENERATORS = {"formulas": formulas}


def _at(x: Value, t: int) -> Fraction:
    if isinstance(x, (list, tuple)):
        return exact(x[t])
    return exact(x)


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


RULEBOOKS = {"held_balance": held_balance, "settle": settle, "transfer": transfer, "write_down": write_down,
             "asset_pool": asset_pool}
