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
* the rulebooks ``held_balance`` (a working-capital account held at a
  target each period), ``settle`` (payables paid in full in the period they
  arise) and ``transfer`` (a stated amount posted between accounts each
  period) began here and moved to :mod:`one_engine.rulebooks.postings` on
  2026-10-14, because three or more archetypes declare each;
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
conditional ``a if c else b``, ``min``, ``max``, ``abs``, Excel's date
functions over its day counts, spelled as Excel spells them
(``EOMONTH(d, k)``, ``DATE(y, m, d)``, ``YEAR(d)``, ``MONTH(d)``,
``DAY(d)``: a date is the day count Excel stores,
1 being 1 January 1900, so days between two dates are their difference), and
``lag(name, k)``:
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
import datetime
import math
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Union

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.units import exact

Value = Union[Fraction, List[Fraction]]

#: day 0 of Excel's 1900 date system, as Excel counts from 1 March 1900 on (it counts a 29 February
#: 1900 that never was, so its earlier serials are a day off; no model here dates anything that early)
_EXCEL_DAY0 = datetime.date(1899, 12, 30)


def _day(serial: Fraction) -> datetime.date:
    """The calendar date of an Excel day count; a fraction of a day (a time) is dropped, as INT does."""
    return _EXCEL_DAY0 + datetime.timedelta(days=math.floor(serial))


def _serial(d: datetime.date) -> Fraction:
    return Fraction((d - _EXCEL_DAY0).days)


def _eomonth(serial: Fraction, months: Fraction) -> Fraction:
    """Excel's EOMONTH: the last day of the month ``months`` after (or before) the date's month."""
    d = _day(serial)
    k = d.year * 12 + d.month - 1 + math.trunc(months)
    first_of_next = datetime.date(k // 12 + (k % 12 + 1) // 12, (k % 12 + 1) % 12 + 1, 1)
    return _serial(first_of_next - datetime.timedelta(days=1))


def _date(y: Fraction, m: Fraction, d: Fraction) -> Fraction:
    """Excel's DATE for a real calendar date (Excel's rolling of a month 13 or a day 0 is not copied:
    such a date refuses)."""
    return _serial(datetime.date(int(y), int(m), int(d)))


_FUNCS = {"min": min, "max": max, "abs": abs,
          # dates as Excel's day counts (a workbook's typed dates arrive as such)
          # spelled as Excel spells them, so they never take a name a case already uses (month, year)
          "EOMONTH": _eomonth, "DATE": _date, "YEAR": lambda s: _day(s).year,
          "MONTH": lambda s: _day(s).month, "DAY": lambda s: _day(s).day}
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
        # each literal becomes its exact number once, from its own text (0.1 is 1/10), rather than at
        # every period: a workbook translation evaluates formulas with thousands of literals each
        for n in ast.walk(self.tree):
            if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and isinstance(n.value, (int, float)):
                n.value = exact(ast.get_source_segment(self.text, n) or repr(n.value))

    def names(self) -> set:
        return {n.id for n in ast.walk(self.tree) if isinstance(n, ast.Name)} - set(_FUNCS) - {"lag", "cumsum"}

    def lags(self) -> bool:
        """Whether the formula reads other periods (``lag``, ``cumsum``): it is then a series."""
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("lag", "cumsum")
                   for n in ast.walk(self.tree))

    def at(self, t: int, ns: Mapping[str, Value]) -> Fraction:
        if self._fn is None:
            self._fn = self._compile(self.tree)
        return self._fn(t, ns)

    _fn = None

    def _compile(self, n: ast.AST):
        """The node as a function of (t, ns), built once: the same rules and refusals as walking the
        tree at every period, without re-deciding each node's kind each time (a workbook translation
        evaluates a formula at every period, hundreds of thousands of times in all)."""
        where, text = self.where, self.text
        if isinstance(n, ast.BinOp) and type(n.op) in (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod):
            a, b = self._compile(n.left), self._compile(n.right)
            if isinstance(n.op, (ast.Div, ast.Mod)):
                div = isinstance(n.op, ast.Div)

                def quotient(t, ns):
                    x, y = a(t, ns), b(t, ns)
                    if y == 0:
                        raise InputError(f"{where}: division by zero in {text!r} at period {t}")
                    return x / y if div else x % y
                return quotient
            if isinstance(n.op, ast.Add):
                return lambda t, ns: a(t, ns) + b(t, ns)
            if isinstance(n.op, ast.Sub):
                return lambda t, ns: a(t, ns) - b(t, ns)
            return lambda t, ns: a(t, ns) * b(t, ns)
        if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Pow):
            a, b = self._compile(n.left), self._compile(n.right)

            def power(t, ns):
                x, y = a(t, ns), b(t, ns)
                if y.denominator != 1 or (x == 0 and y < 0):
                    raise InputError(f"{where}: {x} ** {y} is not exact ({text!r} at period {t})")
                return x ** int(y)
            return power
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub, ast.UAdd)):
            v = self._compile(n.operand)
            return (lambda t, ns: -v(t, ns)) if isinstance(n.op, ast.USub) else v
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in _CMP:
            cmp, a, b = _CMP[type(n.ops[0])], self._compile(n.left), self._compile(n.comparators[0])
            return lambda t, ns: Fraction(int(cmp(a(t, ns), b(t, ns))))
        if isinstance(n, ast.IfExp):
            test, body, orelse = self._compile(n.test), self._compile(n.body), self._compile(n.orelse)
            return lambda t, ns: body(t, ns) if test(t, ns) else orelse(t, ns)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "lag" and not n.keywords:
            name = n.args[0].id if len(n.args) == 2 and isinstance(n.args[0], ast.Name) else None
            k = self._compile(n.args[1]) if name is not None else None

            def lag(t, ns):
                if name is None or name not in ns:
                    raise InputError(f"{where}: lag takes an earlier value's name and a number of periods ({text!r})")
                periods = k(t, ns)
                if periods.denominator != 1 or periods < 0:
                    raise InputError(f"{where}: lag by {periods} periods is not a whole number of periods ({text!r})")
                back = t - int(periods)
                v = ns[name]
                return Fraction(0) if back < 0 else (v[back] if isinstance(v, list) else v)
            return lag
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "cumsum" and not n.keywords:
            name = n.args[0].id if len(n.args) == 1 and isinstance(n.args[0], ast.Name) else None

            def cumsum(t, ns):
                if name is None or name not in ns:
                    raise InputError(f"{where}: cumsum takes an earlier value's name ({text!r})")
                v = ns[name]
                # a number is the same every period: its running total is that many of it
                return sum(v[:t + 1], Fraction(0)) if isinstance(v, list) else v * (t + 1)
            return cumsum
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in _FUNCS and not n.keywords:
            fn, args = _FUNCS[n.func.id], [self._compile(a) for a in n.args]
            return lambda t, ns: Fraction(fn(*[a(t, ns) for a in args]))
        if isinstance(n, ast.Constant) and not isinstance(n.value, bool) and isinstance(n.value, (int, float, Fraction)):
            # literals were made exact from their own text when the formula was parsed
            value = n.value if isinstance(n.value, Fraction) else exact(repr(n.value))
            return lambda t, ns: value
        if isinstance(n, ast.Name):
            if n.id == "t":
                return lambda t, ns: Fraction(t)
            name = n.id

            def read(t, ns):
                if name not in ns:
                    raise InputError(f"{where}: '{name}' is neither an input nor an earlier value ({text!r})")
                v = ns[name]
                return v[t] if isinstance(v, list) else v
            return read
        kind = type(n).__name__

        def refuse(t, ns):
            raise InputError(f"{where}: {kind} is not allowed in a formula ({text!r})")
        return refuse


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


RULEBOOKS = {"write_down": write_down, "asset_pool": asset_pool}
