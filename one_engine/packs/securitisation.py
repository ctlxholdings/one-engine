"""Securitisation pack: a vehicle run by Hastructure 0.52.3's rules, posted to the book.

What this pack is for
---------------------
esco-product runs its securitisation vehicles (a warehouse that buys a pool,
a direct purchase, a pool of six purchased one by one, the NYSERDA 2020A
green bonds) on Hastructure, the Haskell cash-flow engine behind the AbsBox
Python client, version 0.52.3. Its recorded runs are referees here
(``tests/test_case_esco_absbox.py``): the same deal, stated in the same
words, must pay every bond the same cents on the same dates. This pack is
the interpreter that makes that true, and the ``vehicle`` rulebook that
posts what the interpreter computes to a one_engine book.

The deal is stated as esco-product hands it to AbsBox: the plain
``Generic`` deal (dates, accounts, bonds, fees, waterfall, collection rules,
liquidity, ledgers, status) plus the pool's assets, an optional pool
assumption, revolving pools and bond draws. The vocabulary is AbsBox's; each
term is read here exactly as AbsBox translates it for Hastructure and as
Hastructure 0.52.3 executes it. A term this pack has not built refuses by
name: a near-miss reading would pay a wrong cent that still ties.

Numbers
-------
Hastructure holds balances in cents and rates in millionths, and every
conversion from an exact ratio *floors* (Haskell's ``fromRational`` for a
fixed-point type). So: a bond's interest is ``floor(balance x year fraction
x rate)`` to the cent; a pro-rata split floors each share and adds the
rounding to the first; a formula is evaluated exactly and floored to the cent
where it becomes an amount. Floats in the deal are read as the decimal text
Python prints for them (``repr``), which is the text ``json.dumps`` sends and
Hastructure parses; reading them bit for bit (:func:`one_engine.units.exact`)
would be a different number. Three places compute in binary floating point
because Hastructure does: a level payment (``calcPmt``), a CPR turned into a
period rate, and nothing else.

The run
-------
Hastructure is event driven, not period driven. The interpreter builds the
deal's events from its dates (pool collections on the pool frequency, the
waterfall on the payment frequency, the closing, fee accruals, bank-interest
settlements, bond draws), orders them as Hastructure does on a shared date
(bank interest first, then pool collection, the waterfall, the closing, a
draw, a fee accrual), and runs them until the dates run out or the deal has
no pool flow left and every account is empty (and it is not revolving or
warehousing). Each cash movement is journalled with its date.

What is posted
--------------
Every journalled movement becomes one posting in the period whose end date
(``period_ends``) is the first on or after the movement's date. The rulebook
declares its own accounts:

* ``acc.<name>`` (asset): each Hastructure account;
* ``pool.<id>`` (asset): the outstanding balance of the assets a pool holds;
* ``bond.<name>`` (liability): each bond's balance, equity tranche included;
* ``liq.<name>`` (liability): what a liquidity provider has paid in;
* ``pool_interest``, ``pool_recoveries``, ``bank_interest`` (income);
* ``bond_interest``, ``fee.<name>``, ``pool_loss``, ``purchase_difference`` (expense);
* ``distributions`` (equity): the equity tranche's residual yield;
* ``opening_equity`` (equity): what balances the vehicle's opening state.

The opening state (account balances, bond balances, the held pool at the
cutoff) is the book's opening balance sheet. Interest and fees are posted
when paid (cash basis): what is due but unpaid sits in the interpreter and
in the records, not in the book. A ledger (Hastructure's memo book, e.g. the
NYSERDA ``xr`` that carries the amount swept for redemption) posts nothing
and is recorded.

Records
-------
Each period's record (kind = the rulebook's name) carries ``payments`` (a
list of ``[date, bond, kind, amount]``, kind ``int``, ``prin`` or
``yield``), ``collections`` (source -> amount), ``fees_paid``,
``bank_interest`` (account -> amount), ``liquidity`` (provider -> amount
drawn), ``purchases`` (``[date, pool, price, balance bought]``), ``fundings``,
``ledgers`` (name -> ``[direction, amount]`` at the period's end),
``due_interest`` (bond -> interest due unpaid at the period's end) and
``status``.

Known Hastructure 0.52.3 behaviours kept on purpose
---------------------------------------------------
* A liquidity draw does not reduce the provider's available credit
  (``CreditEnhancement.draw`` returns the old ``liqCredit``): each call can
  draw up to the original credit again. esco-product's NYSERDA call
  scenarios ran on that engine, so the referee carries it.
* A recurring fee accrues once per pattern date through an accrual event,
  whether or not the waterfall pays it.

Not built (refuses): recoveries with a lag or rate, prepayment other than a
CPR, default other than ``ByAmount``, assets other than a level Mortgage and
a projected cash flow, ``AdjustRateMortgage``, merging a purchase into a
pool that already holds assets, liquidity to anything but one account,
waterfall supports, bond groups, rate resets, triggers, calls.
"""
from __future__ import annotations

import bisect
import calendar
import datetime as _dt
import math
from decimal import Decimal
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting

SOURCE = "securitisation"
CENT, MICRO = 100, 1_000_000        # Hastructure's Balance (Centi) and IRate (Micro) resolutions


class VehicleError(ValueError):
    """The run met a state Hastructure would refuse (an overdrawn account)."""


# ── numbers and dates as Hastructure reads them ──────────────────────────────

def _num(x: Any, where: str = "") -> Fraction:
    """A deal number as Hastructure parses it: a float by its printed decimal text, never bit for bit."""
    if isinstance(x, bool) or x is None:
        raise InputError(f"{where}: {x!r} is not a number")
    if isinstance(x, (float, Decimal)):
        return Fraction(repr(x) if isinstance(x, float) else str(x))
    return Fraction(x)


def _cent(x: Fraction) -> Fraction:
    """Floor to the cent (``fromRational`` into ``Centi``)."""
    return Fraction(math.floor(x * CENT), CENT)


def _micro(x: Fraction) -> Fraction:
    """Floor to the millionth (``fromRational`` into ``Micro``)."""
    return Fraction(math.floor(x * MICRO), MICRO)


def _date(x: Any) -> _dt.date:
    return x if isinstance(x, _dt.date) else _dt.date.fromisoformat(str(x))


def _add_months(d: _dt.date, n: int) -> _dt.date:
    """``addGregorianMonthsClip``: the same day n months on, clipped to the month's length."""
    y, m = divmod(d.month - 1 + n, 12)
    y += d.year
    return _dt.date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def _pattern(sd: _dt.date, pat: Any, ed: _dt.date) -> List[_dt.date]:
    """``genSerialDatesTill``: the pattern's dates on or after ``sd``, up to ``ed`` inclusive."""
    if pat == "MonthEnd":
        day = None
    elif isinstance(pat, (list, tuple)) and len(pat) == 2 and pat[0] == "DayOfMonth":
        day = int(pat[1])
    else:
        raise InputError(f"date pattern {pat!r} is not built (MonthEnd, [DayOfMonth, n])")
    out, y, m = [], sd.year, sd.month
    while True:
        d = _dt.date(y, m, calendar.monthrange(y, m)[1] if day is None else day)
        if d > ed:
            return out
        if d >= sd:
            out.append(d)
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def _till_ie(sd: _dt.date, pat: Any, ed: _dt.date) -> List[_dt.date]:
    """``genSerialDatesTill2 IE``: ``sd`` included (prepended when off the pattern), ``ed`` excluded if hit."""
    r = _pattern(sd, pat, ed)
    head, last = bool(r) and r[0] == sd, bool(r) and r[-1] == ed
    if head and last:
        return r[:-1]
    if head:
        return r
    if last:
        return [sd] + r[:-1]
    return [sd] + r


def _months_clip(sd: _dt.date, ed: _dt.date) -> int:
    """Whole months from ``sd`` to ``ed`` (``diffGregorianDurationClip``)."""
    m = (ed.year - sd.year) * 12 + ed.month - sd.month
    while m > 0 and _add_months(sd, m) > ed:
        m -= 1
    return m


def _proj_dates(pat: Any, sd: _dt.date, ed: _dt.date) -> List[_dt.date]:
    """``projDatesByPattern``: the first (months + 1) pattern dates from ``sd``, which may pass ``ed``."""
    n = _months_clip(sd, ed) + 1
    return _pattern(sd, pat, _add_months(ed, n + 2))[:n]


def _year_fraction(dc: str, sd: _dt.date, ed: _dt.date) -> Fraction:
    """Hastructure's ``yearCountFraction`` for the day counts esco-product's deals use."""
    if dc == "DC_ACT_365F":
        return Fraction((ed - sd).days, 365)
    if dc == "DC_30E_360":
        s, e = min(sd.day, 30), min(ed.day, 30)
        return Fraction(e - s + 30 * (ed.month - sd.month) + 360 * (ed.year - sd.year), 360)
    if dc == "DC_30_360_US":
        def feb_end(d: _dt.date) -> bool:
            return d.month == 2 and d.day == calendar.monthrange(d.year, 2)[1]
        s = 30 if (feb_end(sd) or sd.day == 31) else sd.day
        e = 30 if ((ed.day == 31 and sd.day >= 30) or (feb_end(ed) and feb_end(sd))) else ed.day
        return Fraction(e - s + 30 * (ed.month - sd.month) + 360 * (ed.year - sd.year), 360)
    raise InputError(f"day count {dc!r} is not built (DC_ACT_365F, DC_30E_360, DC_30_360_US)")


def _prorata(dues: Sequence[Fraction], amount: Fraction) -> List[Fraction]:
    """``prorataFactors``: each share floored to the cent, the rounding added to the first."""
    total = sum(dues, Fraction(0))
    if total == 0:
        return [Fraction(0)] * len(dues)
    out = [_cent(d / total * min(total, amount)) for d in dues]
    out[0] += amount - sum(out, Fraction(0))
    return out


def _calc_pmt(bal: Fraction, rate: Fraction, periods: int) -> Fraction:
    """``calcPmt``: the level payment, computed in Double as Hastructure does, then floored to the cent."""
    if rate == 0:
        return _cent(bal / periods)
    r = float(rate)
    grown = math.exp(math.log(1 + r) * periods)
    return _cent(bal * Fraction(r * (grown / (grown - 1))))


def _cpr_period(cpr: Fraction, days: int) -> Fraction:
    """``toPeriodRateByInterval``: 1 - (1 - CPR)^(days/365), in Double."""
    return Fraction(1 - float(1 - cpr) ** (days / 365))


# ── assets and their projected flows ─────────────────────────────────────────

class Flow:
    """One row of a pool's cash-flow frame: the balance after it and what it pays."""
    __slots__ = ("d", "bal", "prin", "int", "ppy", "dflt", "rec")

    def __init__(self, d: _dt.date, bal: Fraction, prin=Fraction(0), int_=Fraction(0), ppy=Fraction(0),
                 dflt=Fraction(0), rec=Fraction(0)):
        self.d, self.bal, self.prin, self.int, self.ppy, self.dflt, self.rec = d, bal, prin, int_, ppy, dflt, rec

    def empty(self) -> bool:
        return not (self.prin or self.int or self.ppy or self.dflt or self.rec)

    def begin(self) -> Fraction:
        """The balance before this row: what the asset was worth when its first kept row started."""
        return self.bal + self.prin + self.ppy + self.dflt


_MONTHS = {"Monthly": 1, "Quarterly": 3, "SemiAnnually": 6, "Annually": 12}
_PER_YEAR = {"Monthly": 12, "Quarterly": 4, "SemiAnnually": 2, "Annually": 1}


class Mortgage:
    """A level-payment Mortgage (``AssetClass.Mortgage``), current, fixed rate."""

    def __init__(self, a: Sequence[Any], where: str):
        orig, cur = a[1], a[2]
        if orig.get("type") != "Level" or cur.get("status") != "Current" or orig.get("freq") not in _MONTHS:
            raise InputError(f"{where}: only a current, level Mortgage with a monthly to annual frequency is built")
        self.ob = _cent(_num(orig["originBalance"], where))
        self.ot, self.freq = int(orig["originTerm"]), orig["freq"]
        self.sd = _date(orig["originDate"])
        self.cb = _cent(_num(cur["currentBalance"], where))
        self.cr = _micro(_num(cur["currentRate"], where))
        self.rt = int(cur["remainTerm"])

    def _copy(self, **kw: Any) -> "Mortgage":
        m = object.__new__(Mortgage)
        m.__dict__.update(self.__dict__)
        m.__dict__.update(kw)
        return m

    def split(self, r: Fraction) -> "Mortgage":
        """The share ``r`` of the asset (``splitAssetUnion``): both balances floored to the cent."""
        return self._copy(ob=_cent(self.ob * r), cb=_cent(self.cb * r))

    def _dates(self) -> List[_dt.date]:
        return [self.sd] + [_add_months(self.sd, _MONTHS[self.freq] * i) for i in range(1, self.ot + 1)]

    def align(self, d: _dt.date) -> "Mortgage":
        """``updateOriginDate2``: shift the origin so that the last payment before the remaining term falls on ``d``."""
        bench = list(reversed(self._dates()))[self.rt]
        return self._copy(sd=self.sd + (d - bench))

    def _period_rate(self) -> Fraction:
        n = _PER_YEAR[self.freq]
        return self.cr if n == 1 else _micro(self.cr / n)

    def project(self, as_of: _dt.date, assumption: Optional[Tuple[Fraction, List[Fraction], Fraction]] = None) -> List[Flow]:
        """The remaining schedule from ``as_of`` (``projCashflow`` then ``cutoffTrs``).

        ``assumption`` is ``(default amount, its vector, CPR)``: ``projCashflowByDefaultAmt`` with no
        recovery and no lag. Rows before ``as_of`` are history and are dropped.
        """
        ds = self._dates()[-(self.rt + 1):]
        last, cf = ds[0], ds[1:]
        rate, bal = self._period_rate(), self.cb
        rows = [Flow(last, bal)]
        if assumption is None:
            for i, d in enumerate(cf):
                it = _cent(bal * rate)
                prin = _calc_pmt(bal, rate, self.rt - i) - it
                bal -= prin
                rows.append(Flow(d, bal, prin, it))
            return [x for x in rows if x.d >= as_of]
        dbal, vector, cpr = assumption
        n = len(cf)
        expected = ([_cent(dbal * v) for v in vector] + [Fraction(0)] * n)[:n]
        unapplied, left = [], dbal
        for x in expected:
            left -= x
            unapplied.append(left)
        ppy = [_cpr_period(cpr, (b - a).days) for a, b in zip(ds[:-1], ds[1:])]
        for i, d in enumerate(cf):
            # the last expected default takes whatever balance it meets
            dflt = bal if bal <= expected[i] + unapplied[i] else expected[i]
            pp = _cent(max(Fraction(0), bal - dflt) * ppy[i])
            base = max(Fraction(0), bal - dflt - pp)
            it = _cent(base * rate)
            rt = n - i
            prin = base if rt == 0 else _calc_pmt(base, rate, rt) - it
            bal = base - prin
            rows.append(Flow(d, bal, prin, it, pp, dflt))
        while len(rows) > 1 and rows[-1].empty():          # dropTailEmptyTxns
            rows.pop()
        return [x for x in rows if x.d >= as_of]


class Projected:
    """A projected cash flow (``ProjectedCashflow``): a begin balance and dated principal and interest."""

    def __init__(self, a: Sequence[Any], where: str):
        _, beg, begin_date, flows, _pattern_ = a
        self.cb, self.bd = _cent(_num(beg, where)), _date(begin_date)
        self.flows = [(_date(d), _cent(_num(p, where)), _cent(_num(i, where))) for d, p, i in flows]

    def project(self, as_of: _dt.date, assumption: Any = None) -> List[Flow]:
        if assumption is not None:
            raise InputError("a pool assumption on a projected cash flow is not built")
        bal, rows = self.cb, [Flow(self.bd, self.cb)]
        for d, p, i in self.flows:
            bal -= p
            rows.append(Flow(d, bal, p, i))
        return [x for x in rows if x.d >= as_of]


def _asset(a: Sequence[Any], where: str):
    if a[0] == "Mortgage":
        return Mortgage(a, where)
    if a[0] == "ProjectedCashflow":
        return Projected(a, where)
    raise InputError(f"{where}: asset type {a[0]!r} is not built (Mortgage, ProjectedCashflow)")


def _aggregate(rows: List[Flow], dates: Sequence[_dt.date]) -> List[Flow]:
    """``aggTsByDates``: each date takes the rows dated on or before it not yet taken; a date with none
    after the first gets an empty row."""
    out, i, last = [], 0, None
    for d in dates:
        grp = []
        while i < len(rows) and rows[i].d <= d:
            grp.append(rows[i])
            i += 1
        if grp:
            f = Flow(d, grp[-1].bal)
            for g in grp:
                f.prin += g.prin; f.int += g.int; f.ppy += g.ppy; f.dflt += g.dflt; f.rec += g.rec
            out.append(f)
            last = f
        elif last is not None:
            last = Flow(d, last.bal)
            out.append(last)
    return out


# ── the deal ─────────────────────────────────────────────────────────────────

class _Bond:
    def __init__(self, name: str, b: Mapping[str, Any], where: str):
        self.name, self.bal = name, _cent(_num(b["balance"], where))
        self.rate = _micro(_num(b["rate"], where))
        rt = b.get("rateType") or {}
        self.dc = rt.get("dayCount", "DC_ACT_365F") if isinstance(rt, Mapping) else "DC_ACT_365F"
        bt = b["bondType"]
        if not isinstance(bt, Mapping) or len(bt) != 1 or next(iter(bt)) not in ("Sequential", "Equity"):
            raise InputError(f"{where}: bond type {bt!r} is not built (Sequential, Equity)")
        self.equity = "Equity" in bt
        self.due_int = self.due_ioi = Fraction(0)
        self.due_date = _date(b["lastAccrueDate"]) if b.get("lastAccrueDate") else None
        self.origin = _date(b["startDate"])

    def accrue(self, d: _dt.date) -> None:
        """``accrueInt``: interest from the last accrual (or the start) to ``d``; interest on interest due."""
        begin = self.due_date or self.origin
        if d <= begin:
            return
        yf = _year_fraction(self.dc, begin, d)
        self.due_ioi += _cent(self.due_int * (self.rate * yf))
        self.due_int += _cent(self.bal * yf * self.rate)
        self.due_date = d


_SOURCES = {"CollectedInterest": "int", "CollectedPrincipal": "prin", "CollectedPrepayment": "ppy",
            "CollectedRecoveries": "rec"}
_POOL_SOURCES = {"Interest": "int", "Principal": "prin", "Prepayment": "ppy", "Recoveries": "rec"}
_REVOLVING = ("ConstantAsset", "StaticAsset")


class Vehicle:
    """One Hastructure 0.52.3 run of a deal stated in AbsBox's plain vocabulary.

    ``run()`` returns ``self``; ``journal`` then holds every cash movement as ``(date, debit, credit,
    amount, event)`` over the account names the module docstring lists, ``opening`` the opening balance of
    each, ``dated`` the dated facts the rulebook records, and ``snapshots`` the memo state (ledgers,
    interest due, status) after each requested cut-off date.

    Usage::

        v = Vehicle(spec, assets, pool_assumption, revolving, fundings).run(cutoffs)
    """

    def __init__(self, spec: Mapping[str, Any], assets: Sequence[Any] = (), pool_assumption: Any = None,
                 revolving: Optional[Mapping[str, Mapping[str, Any]]] = None,
                 fundings: Sequence[Mapping[str, Any]] = (), where: str = "vehicle"):
        self.where, self.spec = where, spec
        dt = spec["dates"]
        status = spec["status"]
        if isinstance(status, (list, tuple)):
            if status[0] != "PreClosing":
                raise InputError(f"{where}: status {status!r} is not built ([PreClosing, s] or s)")
            status = ("PreClosing", status[1])
        self.status = status
        self.acc: Dict[str, Fraction] = {}
        self.acc_type: Dict[str, Any] = {}
        self.acc_int: Dict[str, List[Any]] = {}
        for n, a in spec["accounts"]:
            self.acc[n] = _cent(_num(a.get("balance", 0), f"{where}.accounts.{n}"))
            self.acc_type[n] = a.get("type")
            if a.get("interest"):
                i = a["interest"]
                self.acc_int[n] = [_micro(_num(i["rate"], where)), i["period"], _date(i["lastSettleDate"])]
        # the account statement (date, balance after, amount): bank interest weighs balances between its lines
        self.acc_log: Dict[str, List[Tuple[_dt.date, Fraction, Fraction]]] = {n: [] for n in self.acc}
        self.bonds = {n: _Bond(n, b, f"{where}.bonds.{n}") for n, b in spec["bonds"]}
        self.fees: Dict[str, Dict[str, Any]] = {}
        for n, f in spec.get("fees") or ():
            ft = f["type"]
            if not isinstance(ft, Mapping) or list(ft) != ["recurFee"]:
                raise InputError(f"{where}.fees.{n}: fee type {ft!r} is not built (recurFee)")
            self.fees[n] = {"amt": _cent(_num(ft["recurFee"][1], where)), "pat": ft["recurFee"][0],
                            "start": _date(f["feeStart"]), "due": Fraction(0), "last": None}
        self.liq: Dict[str, Fraction] = {}
        for n, x in (spec.get("liquidity") or {}).items():
            if x.get("type") != {"total": x.get("credit")}:
                raise InputError(f"{where}.liquidity.{n}: only a fixed total credit is built")
            self.liq[n] = _cent(_num(x["credit"], where))
        # a ledger opens on the credit side (esco-product's A5 workaround states it so)
        self.ledgers = {n: ["Credit", _cent(_num(x.get("balance", 0), where))]
                        for n, x in (spec.get("ledgers") or {}).items()}
        self.revolving: Dict[str, Tuple[str, list]] = {}
        for n, x in (revolving or {}).items():
            if x["kind"] not in _REVOLVING:
                raise InputError(f"{where}.revolving.{n}: kind must be one of {_REVOLVING}")
            self.revolving[n] = (x["kind"], [_asset(a, f"{where}.revolving.{n}") for a in x["assets"]])

        stated = _date(dt["stated"])
        if "closing" in dt:
            # PreClosingDates: collections from the closing date, payments from the first payment date
            self.closing, start = _date(dt["closing"]), _date(dt["cutoff"])
            pa = _till_ie(self.closing, dt["poolFreq"], stated)
            ba = _till_ie(_date(dt["firstPay"]), dt["payFreq"], stated)
            events = [(d, 1, ("collect",)) for d in pa] + [(d, 2, ("pay",)) for d in ba]
            if isinstance(self.status, tuple):
                events.append((self.closing, 3, ("close",)))
        else:
            # CurrentDates: the deal is already running
            self.closing, start = _date(dt["lastPay"]), _date(dt["lastCollect"])
            pa = _till_ie(_date(dt["nextCollect"]), dt["poolFreq"], stated)
            ba = _till_ie(_date(dt["nextPay"]), dt["payFreq"], stated)
            events = [(d, 1, ("collect",)) for d in pa] + [(d, 2, ("pay",)) for d in ba]
        for n, (_, pat, last) in self.acc_int.items():
            events += [(d, 0, ("earn", n)) for d in _pattern(last, pat, stated)]
        for n, f in self.fees.items():
            events += [(d, 5, ("accrue_fee", n)) for d in _proj_dates(f["pat"], f["start"], stated)]
        for i, x in enumerate(fundings):
            fr = Reader(x, source=f"{where}.fundings[{i}]")
            events.append((_date(fr.take("date")), 4,
                           ("fund", fr.take("bond"), fr.take("account"), _cent(_num(fr.take("amount"), where)))))
            fr.finish()
        # Hastructure's sortActionOnDate on a shared date: interest first, collection before the waterfall,
        # collection before the closing; the rest keep their order
        self.events = sorted(events, key=lambda e: (e[0], e[1]))

        self.pools: Dict[str, List[Flow]] = {}
        self.opening: Dict[str, Fraction] = {}
        if assets:
            by_index = self._assumption(pool_assumption)
            rows: List[Flow] = []
            held = Fraction(0)
            for i, a in enumerate(assets):
                kept = _asset(a, f"{where}.assets[{i}]").project(start, by_index.get(i))
                if kept:
                    held += kept[0].begin()
                rows += kept
            rows.sort(key=lambda x: x.d)
            # withBegTs: a zero row at the cutoff, then the frame on the collection dates
            self.pools["PoolConsol"] = _aggregate([Flow(start, Fraction(0))] + rows, pa)
            self.opening["pool.PoolConsol"] = held
        elif pool_assumption is not None:
            raise InputError(f"{where}: a pool assumption without a held pool")
        for n, x in self.acc.items():
            self.opening[f"acc.{n}"] = x
        for n, b in self.bonds.items():
            self.opening[f"bond.{n}"] = b.bal
        self.journal: List[Tuple[_dt.date, str, str, Fraction, str]] = []
        self.dated: List[Tuple[_dt.date, str, Any]] = []
        self.collected: Dict[str, List[Flow]] = {}
        self.snapshots: List[Dict[str, Any]] = []

    def _assumption(self, x: Any) -> Dict[int, Tuple[Fraction, List[Fraction], Fraction]]:
        """AbsBox's ``("ByIndex", ([i...], (("Mortgage", {"ByAmount": (amt, vector)}, {"CPR": r}, None, None), None, None)), ...)``."""
        if x is None:
            return {}
        if x[0] != "ByIndex":
            raise InputError(f"{self.where}: pool assumption {x[0]!r} is not built (ByIndex)")
        out = {}
        for idxs, (perf, delinq, extra) in x[1:]:
            kind, dflt, ppy, rec, stress = perf
            if kind != "Mortgage" or rec is not None or stress is not None or delinq is not None or extra is not None:
                raise InputError(f"{self.where}: only a Mortgage default-by-amount and CPR assumption is built")
            if list(dflt) != ["ByAmount"] or list(ppy) != ["CPR"]:
                raise InputError(f"{self.where}: default {list(dflt)} / prepayment {list(ppy)} is not built (ByAmount, CPR)")
            amt, vector = dflt["ByAmount"]
            for i in idxs:
                out[int(i)] = (_cent(_num(amt)), [_num(v) for v in vector], _num(ppy["CPR"]))
        return out

    # ── journal ──────────────────────────────────────────────────────────────

    def _post(self, d: _dt.date, debit: str, credit: str, amount: Fraction, event: str) -> None:
        if amount < 0:
            debit, credit, amount = credit, debit, -amount
        if amount:
            self.journal.append((d, debit, credit, amount, event))

    def _line(self, an: str, d: _dt.date, amt: Fraction, drawn: bool = False) -> None:
        """Move an account and write its statement line (the lines bank interest weighs balances between).

        A draw beyond the balance refuses, as ``Accounts.draw`` does; a deposit is never checked (a pool row
        can carry a negative amount, and Hastructure deposits it as it is)."""
        if drawn and self.acc[an] + amt < 0:
            raise VehicleError(f"{self.where} {d}: draw {-amt} from {an} with balance {self.acc[an]}")
        self.acc[an] += amt
        self.acc_log[an].append((d, self.acc[an], amt))

    def _dep(self, an: str, d: _dt.date, amt: Fraction, credit: str, event: str) -> None:
        """``Accounts.deposit``: always a statement line, even for nothing."""
        self._line(an, d, amt)
        self._post(d, f"acc.{an}", credit, amt, event)

    def _draw(self, an: str, d: _dt.date, amt: Fraction, debit: str, event: str) -> None:
        """``Accounts.draw``: nothing drawn leaves no line; more than the balance refuses."""
        if amt:
            self._line(an, d, -amt, drawn=True)
            self._post(d, debit, f"acc.{an}", amt, event)

    def _transfer(self, src: str, dst: str, d: _dt.date, amt: Fraction) -> None:
        """``Accounts.transfer``: a draw from ``src`` (no line if nothing) and a deposit to ``dst`` (always a line)."""
        if amt:
            self._line(src, d, -amt, drawn=True)
        self._line(dst, d, amt)
        self._post(d, f"acc.{dst}", f"acc.{src}", amt, f"transfer:{src}>{dst}")

    # ── the run ──────────────────────────────────────────────────────────────

    def run(self, cutoffs: Sequence[_dt.date] = ()) -> "Vehicle":
        """Run every event; snapshot the memo state after each cut-off date in ``cutoffs`` (ascending)."""
        cut = list(cutoffs)
        k = 0
        for d, _, ev in self.events:
            while k < len(cut) and d > cut[k]:
                self.snapshots.append(self._snapshot())
                k += 1
            if self._ended():
                break
            kind = ev[0]
            if kind == "collect":
                self._collect(d)
            elif kind == "pay":
                for a in self.spec["waterfall"].get(self._waterfall_key(), []):
                    self._act(d, a)
            elif kind == "close":
                for a in self.spec["waterfall"].get("closingDay", []):
                    self._act(d, a)
                self.dated.append((d, "status", self.status[1]))
                self.status = self.status[1]
            elif kind == "earn":
                self._earn(d, ev[1])
            elif kind == "accrue_fee":
                self._calc_fee(d, ev[1])
            else:
                _, bond, an, amt = ev
                b = self.bonds[bond]
                b.accrue(d)
                b.bal += amt
                self._dep(an, d, amt, f"bond.{bond}", f"fund:{bond}")
                self.dated.append((d, "funding", [bond, amt]))
        while k < len(cut):
            self.snapshots.append(self._snapshot())
            k += 1
        return self

    def _snapshot(self) -> Dict[str, Any]:
        return {"ledgers": {n: list(x) for n, x in self.ledgers.items()},
                "due_interest": {n: b.due_int + b.due_ioi for n, b in self.bonds.items()},
                "status": self.status if isinstance(self.status, str) else list(self.status)}

    def _ended(self) -> bool:
        """No pool flow left, every account empty, and not revolving or warehousing (or still pre-closing)."""
        if isinstance(self.status, tuple) or self.status in ("Revolving", "Warehousing"):
            return False
        return (all(x.empty() for rows in self.pools.values() for x in rows)
                and sum(self.acc.values(), Fraction(0)) == 0)

    def _collect(self, d: _dt.date) -> None:
        """``PoolCollection``: every row dated on or before ``d`` is collected under the collection rules."""
        if not any(self.pools.values()):
            return
        for pid, rows in self.pools.items():
            got = [x for x in rows if x.d <= d]
            self.pools[pid] = [x for x in rows if x.d > d]
            self.collected.setdefault(pid, []).extend(got)
            dflt = sum((x.dflt for x in got), Fraction(0))
            self._post(d, "pool_loss", f"pool.{pid}", dflt, f"default:{pid}")
            if dflt:
                self.dated.append((d, "default", [pid, dflt]))
            collected = set()
            for rule in self.spec["collection"]:
                if len(rule) == 3:
                    pids, src, an = rule
                else:
                    pids, (src, an) = None, rule
                if pids is not None and pid not in pids:
                    continue
                if src not in _SOURCES:
                    raise InputError(f"{self.where}: collection source {src!r} is not built {tuple(_SOURCES)}")
                collected.add(_SOURCES[src])
                amt = sum((getattr(x, _SOURCES[src]) for x in got), Fraction(0))
                credit = {"int": "pool_interest", "rec": "pool_recoveries"}.get(_SOURCES[src], f"pool.{pid}")
                self._dep(an, d, amt, credit, f"collect:{pid}:{src}")
                if amt:
                    self.dated.append((d, "collection", [src, amt]))
            lost = [k for k in ("int", "prin", "ppy", "rec") if k not in collected and any(getattr(x, k) for x in got)]
            if lost:
                # Hastructure would let the cash vanish; a book cannot, so the deal must state where it goes
                raise InputError(f"{self.where} {d}: pool {pid} pays {lost} that no collection rule collects")

    def _earn(self, d: _dt.date, an: str) -> None:
        """``depositInt`` (BankAccount): the 30E/360-weighted balance since the last settlement x the rate."""
        rate, _, last = self.acc_int[an]
        log = self.acc_log[an]
        if not log:
            x = _cent(_cent(self.acc[an] * rate) * _year_fraction("DC_30E_360", last, d))
        else:
            # each balance between statement lines weighs its own (floored) year fraction
            win = [t for t in log if last <= t[0] < d]
            bals = [b - a for _, b, a in win] + [self.acc[an]]
            ds = [last] + [t[0] for t in win] + [d]
            avg = sum((_cent(b * _year_fraction("DC_30E_360", s, e)) for b, s, e in zip(bals, ds[:-1], ds[1:])),
                      Fraction(0))
            x = _cent(avg * rate)
        self.acc_int[an][2] = d
        self._dep(an, d, x, "bank_interest", f"bank_interest:{an}")
        if x:
            self.dated.append((d, "bank_interest", [an, x]))

    def _calc_fee(self, d: _dt.date, fn: str) -> None:
        """``calcDueFee`` (RecurFee): one amount per pattern date from the last accrual (or start + 1 day) to ``d``."""
        f = self.fees[fn]
        sd = f["last"] or (f["start"] + _dt.timedelta(days=1))
        n = len(_pattern(sd, f["pat"], d)) if sd <= d else 0
        if n:
            f["due"] += f["amt"] * n
            f["last"] = d + _dt.timedelta(days=1)

    def _waterfall_key(self) -> Optional[str]:
        """The waterfall AbsBox files for the status: ``DistributionDay|<s>``, ``<s>``, or ``amortizing``."""
        w = self.spec["waterfall"]
        for k in (f"DistributionDay|{self.status}", self.status, self.status.lower()):
            if k in w:
                return k
        return "amortizing" if "amortizing" in w else None

    # ── formulas, conditions, reserves ───────────────────────────────────────

    def _q(self, d: _dt.date, ds: Sequence[Any]) -> Fraction:
        """``queryCompound`` over AbsBox's formula words, exact."""
        op, args = ds[0], list(ds[1:])
        if op == "bondBalance":
            return sum((self.bonds[n].bal for n in (args or list(self.bonds))), Fraction(0))
        if op == "bondDueInt":
            return sum((self.bonds[n].due_int for n in args), Fraction(0))
        if op == "accountBalance":
            return sum((self.acc[n] for n in args), Fraction(0))
        if op == "ledgerBalance":
            return sum((self.ledgers[n][1] for n in args), Fraction(0))
        if op == "factor":
            return self._q(d, args[0]) * _num(args[1])
        if op == "+":
            return sum((self._q(d, x) for x in args), Fraction(0))
        if op == "-":
            return self._q(d, args[0]) - sum((self._q(d, x) for x in args[1:]), Fraction(0))
        if op == "floorWithZero":
            return max(Fraction(0), self._q(d, args[0]))
        if op == "const":
            return _num(args[0])
        if op in ("reserveGap", "reserveExcess"):
            target = sum((self._target(d, n) for n in args), Fraction(0))
            held = self._q(d, ["accountBalance", *args])
            return max(Fraction(0), target - held if op == "reserveGap" else held - target)
        if op == "cumPoolCollection":
            pids, srcs = args[0], args[1:]
            return sum((getattr(x, _POOL_SOURCES[s]) for pid, rows in self.collected.items()
                        if not pids or pid in pids for x in rows for s in srcs), Fraction(0))
        raise InputError(f"{self.where}: formula {op!r} is not built")

    def _target(self, d: _dt.date, an: str) -> Fraction:
        """``calcTargetAmount``: a reserve account's target (fix, % of a formula, max, min); 0 if none."""
        def ev(t: Sequence[Any]) -> Fraction:
            if t[0] == "max":
                return max(ev(x) for x in t[1:])
            if t[0] == "min":
                return min(ev(x) for x in t[1:])
            if t[0] == "fix":
                return _cent(_num(t[1]))
            if t[0] == "target":
                return _cent(self._q(d, t[1]) * _num(t[2]))
            raise InputError(f"{self.where}: reserve type {t[0]!r} is not built")
        t = self.acc_type.get(an)
        return ev(t) if t else Fraction(0)

    def _test(self, d: _dt.date, p: Sequence[Any]) -> bool:
        """AbsBox's ``mkPre``: ``all``, a date test, ``date in``, or ``[formula, op, number]``."""
        if p[0] == "all":
            return all(self._test(d, x) for x in p[1:])
        if p[0] == "date" and p[1] == "in":
            return d in [_date(x) for x in p[2:]]
        if isinstance(p[0], (list, tuple)):
            left, op, right = self._q(d, p[0]), p[1], _num(p[2])
        else:
            left, op, right = d, p[0], _date(p[1])
        compare = {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right}
        if op not in compare:
            raise InputError(f"{self.where}: condition {p!r} is not built")
        return compare[op]

    def _limit(self, d: _dt.date, lim: Optional[Mapping[str, Any]], avail: Fraction) -> Fraction:
        """``calcAvailAfterLimit`` with no support: the limit's amount (floored), capped by what is available."""
        if not lim:
            return avail
        if list(lim) == ["formula"]:
            return min(_cent(self._q(d, lim["formula"])), avail)
        if list(lim) == ["balCapAmt"]:
            return min(_cent(_num(lim["balCapAmt"])), avail)
        raise InputError(f"{self.where}: limit {lim!r} is not built (formula, balCapAmt)")

    def _book(self, ln: str, dr: str, amt: Fraction) -> None:
        """``entryLogByDr``: a ledger keeps one side and an amount; booking the other side nets or flips it."""
        side, bal = self.ledgers[ln]
        if side == dr:
            self.ledgers[ln] = [side, bal + amt]
        elif amt > bal:
            self.ledgers[ln] = [dr, amt - bal]
        else:
            self.ledgers[ln] = [side, bal - amt]

    # ── waterfall actions ────────────────────────────────────────────────────

    def _act(self, d: _dt.date, a: Sequence[Any]) -> None:
        op = a[0]
        if op == "If":
            if self._test(d, a[1]):
                for b in a[2:]:
                    self._act(d, b)
        elif op == "changeStatusIf":
            if self._test(d, a[1]):
                self.dated.append((d, "status", a[2]))
                self.status = a[2]
        elif op == "calcInt":
            for n in a[1:]:
                b = self.bonds[n]
                if b.equity or (b.due_date is None and b.bal + b.due_int == 0):
                    continue
                if b.due_date is None:
                    b.due_date = self.closing
                b.accrue(d)
        elif op == "calcFee":
            for n in a[1:]:
                self._calc_fee(d, n)
        elif op == "transfer":
            src, dst = a[1], a[2]
            amt = self._limit(d, a[3] if len(a) > 3 else None, self.acc[src])
            self._transfer(src, dst, d, amt)
            if len(a) == 7 and a[4] == "book":
                self._book(a[6], a[5], amt)
            elif len(a) not in (3, 4):
                raise InputError(f"{self.where}: transfer {a!r} is not built")
        elif op == "bookBy":
            kind, ln, dr, ds = a[1]
            if kind != "ds":
                raise InputError(f"{self.where}: bookBy {kind!r} is not built (ds)")
            self._book(ln, dr, _cent(self._q(d, ds)))
        elif op == "liqSupport":
            pn, kind, ans = a[1], a[2], a[3]
            if kind != "account" or len(ans) != 1:
                raise InputError(f"{self.where}: liqSupport to {kind!r} {ans!r} is not built (one account)")
            lim = a[4] if len(a) > 4 else None
            if lim and list(lim) != ["formula"]:
                raise InputError(f"{self.where}: liqSupport limit {lim!r} is not built (formula)")
            amt = self.liq[pn] if not lim else _cent(min(self.liq[pn], self._q(d, lim["formula"])))
            # 0.52.3 keeps the credit unchanged after a draw (module docstring)
            self._dep(ans[0], d, amt, f"liq.{pn}", f"liquidity:{pn}")
            self.dated.append((d, "liquidity", [pn, amt]))
        elif op == "payFee":
            an, names = a[1], a[2]
            if len(a) > 3:
                raise InputError(f"{self.where}: payFee with a limit or support is not built")
            dues = [self.fees[n]["due"] for n in names]
            amt = min(self.acc[an], sum(dues, Fraction(0)))
            for n, x in zip(names, _prorata(dues, amt)):
                self.fees[n]["due"] -= x
                self._draw(an, d, x, f"fee.{n}", f"fee:{n}")
                if x:
                    self.dated.append((d, "fee", [n, x]))
        elif op == "payInt":
            an, names = a[1], a[2]
            if len(a) > 3:
                raise InputError(f"{self.where}: payInt with a limit or support is not built")
            bs = [self.bonds[n] for n in names]
            dues = [b.due_ioi + b.due_int for b in bs]
            amt = min(self.acc[an], sum(dues, Fraction(0)))
            for b, x in zip(bs, _prorata(dues, amt)):
                ioi = min(x, b.due_ioi)
                b.due_ioi -= ioi
                b.due_int -= x - ioi
                self._draw(an, d, x, "bond_interest", f"int:{b.name}")
                if x:
                    self.dated.append((d, "payment", [b.name, "int", x]))
        elif op == "payPrin":
            an, names = a[1], a[2]
            bs = [self.bonds[n] for n in names if self.bonds[n].bal > 0]
            dues = [b.bal for b in bs]
            amt = min(self.acc[an], sum(dues, Fraction(0)))
            if len(a) > 3:
                if set(a[3]) != {"limit"}:
                    raise InputError(f"{self.where}: payPrin with {sorted(a[3])} is not built (limit)")
                amt = self._limit(d, a[3]["limit"], amt)
            for b, x in zip(bs, _prorata(dues, amt)):
                b.bal -= x
                self._draw(an, d, x, f"bond.{b.name}", f"prin:{b.name}")
                if x:
                    self.dated.append((d, "payment", [b.name, "prin", x]))
        elif op == "payIntResidual":
            an, n = a[1], a[2]
            x = self.acc[an]
            self._draw(an, d, x, "distributions", f"yield:{n}")
            if x:
                self.dated.append((d, "payment", [n, "yield", x]))
        elif op in ("buyAsset", "buyAssetFromTo"):
            self._buy(d, a)
        else:
            raise InputError(f"{self.where}: waterfall action {op!r} is not built")

    def _buy(self, d: _dt.date, a: Sequence[Any]) -> None:
        """``BuyAsset`` / ``BuyAssetFrom``: buy from a revolving pool with an account's cash.

        What can be spent is the limit capped by the account. The pool is valued at its current balance x
        the pricing factor (each asset floored to the cent). A ConstantAsset pool sells exactly what is
        spent, an equal share of each asset; a StaticAsset pool sells at most its value and keeps the rest.
        The assets bought are aligned to the purchase date and projected from it.
        """
        pricing, an, lim = a[1], a[2], a[3]
        src = a[4] if a[0] == "buyAssetFromTo" else "Consol"
        tgt = a[5] if a[0] == "buyAssetFromTo" else "PoolConsol"
        if pricing[0] != "Current|Defaulted" or len(pricing) != 3:
            raise InputError(f"{self.where}: pricing {pricing!r} is not built (Current|Defaulted factor)")
        kind, assets = self.revolving[src]
        avail = self._limit(d, lim, self.acc[an])
        assets = [x.align(d) for x in assets]
        value = sum((_cent(x.cb * _num(pricing[1])) for x in assets), Fraction(0))
        price = avail if kind == "ConstantAsset" else min(avail, value)
        r = price / value
        if kind == "ConstantAsset":
            bought = [x.split(r) for x in assets]
        else:
            bought = [x.split(Fraction(1) if r >= 1 else r) for x in assets]
            self.revolving[src] = (kind, [x.split(Fraction(0) if r >= 1 else 1 - r) for x in assets])
        balance = sum((x.cb for x in bought), Fraction(0))
        self._draw(an, d, price, f"pool.{tgt}", f"buy:{tgt}")
        self._post(d, "purchase_difference", f"pool.{tgt}", price - balance, f"buy:{tgt}:difference")
        self.dated.append((d, "purchase", [tgt, price, balance]))
        rows: List[Flow] = []
        for x in bought:
            rows += x.align(d).project(d)
        rows.sort(key=lambda x: x.d)
        if self.pools.get(tgt):
            raise InputError(f"{self.where}: buying into pool {tgt!r}, which already holds assets, is not built")
        self.pools[tgt] = _aggregate(rows, sorted({x.d for x in rows}))

    # ── what the rulebook declares ───────────────────────────────────────────

    def accounts(self) -> Dict[str, Nature]:
        """Every account the run can post to, with its nature."""
        out = {f"acc.{n}": Nature.ASSET for n in self.acc}
        out.update({f"bond.{n}": Nature.LIABILITY for n in self.bonds})
        out.update({f"liq.{n}": Nature.LIABILITY for n in self.liq})
        out.update({f"fee.{n}": Nature.EXPENSE for n in self.fees})
        pools = ({"PoolConsol"} if "pool.PoolConsol" in self.opening else set()) | _purchase_targets(self.spec)
        out.update({f"pool.{p}": Nature.ASSET for p in pools})
        out.update({"pool_interest": Nature.INCOME, "pool_recoveries": Nature.INCOME, "bank_interest": Nature.INCOME,
                    "bond_interest": Nature.EXPENSE, "pool_loss": Nature.EXPENSE,
                    "purchase_difference": Nature.EXPENSE, "distributions": Nature.EQUITY,
                    "opening_equity": Nature.EQUITY})
        return out


def _purchase_targets(spec: Mapping[str, Any]) -> set:
    """The pools a waterfall buys into (``PoolConsol`` for ``buyAsset``)."""
    out = set()

    def walk(a: Any) -> None:
        if isinstance(a, (list, tuple)) and a:
            if a[0] == "buyAsset":
                out.add("PoolConsol")
            elif a[0] == "buyAssetFromTo":
                out.add(a[5])
            else:
                for b in a:
                    walk(b)
    for actions in spec["waterfall"].values():
        walk(actions)
    return out


# ── the rulebook ─────────────────────────────────────────────────────────────

def vehicle(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """A Hastructure 0.52.3 vehicle posted to one book; see the module docstring.

    Inputs: ``entity``, ``phase``, ``spec`` (AbsBox's plain deal), ``period_ends`` (one ISO date per
    period: a movement posts in the first period ending on or after its date), and optionally ``assets``
    (the held pool), ``pool_assumption``, ``revolving`` (name -> ``{kind: ConstantAsset | StaticAsset,
    assets}``) and ``fundings`` (``[{date, bond, account, amount}]``: the issueBond schedule).
    """
    name, where = ctx["name"], f"vehicle {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    spec = r.take("spec")
    ends = [_date(x) for x in r.take("period_ends")]
    if len(ends) != group.periods or ends != sorted(set(ends)):
        raise InputError(f"{where}: period_ends must be {group.periods} ascending dates, one per period")
    v = Vehicle(spec,
                r.take_or_default("assets", [], reason="no pool held from the cutoff").value,
                r.take_or_default("pool_assumption", None, reason="the pool pays as scheduled").value,
                r.take_or_default("revolving", {}, reason="no revolving pool").value,
                r.take_or_default("fundings", [], reason="no bond is drawn after the start").value,
                where=where).run(ends)
    if v.journal and v.journal[-1][0] > ends[-1]:
        raise InputError(f"{where}: the run moves cash on {v.journal[-1][0]}, after the last period end {ends[-1]}")
    unit = ctx["currency"]
    natures = v.accounts()
    plug = sum(((1 if natures[a].debit_natured else -1) * x for a, x in v.opening.items()), Fraction(0))
    for acct, nature in natures.items():
        if acct in v.opening:
            group.open(entity, acct, nature, unit, v.opening[acct])
        elif acct == "opening_equity":
            group.open(entity, acct, nature, unit, plug)
        else:
            group.declare(entity, acct, nature, unit)
    by_period: Dict[int, List[Tuple[int, Tuple[_dt.date, str, str, Fraction, str]]]] = {}
    for i, row in enumerate(v.journal):
        by_period.setdefault(bisect.bisect_left(ends, row[0]), []).append((i, row))
    facts: Dict[int, List[Tuple[_dt.date, str, Any]]] = {}
    for row in v.dated:
        facts.setdefault(bisect.bisect_left(ends, row[0]), []).append(row)

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for i, (d, debit, credit, amount, event) in by_period.get(t, []):
            eff = {x: (1 if book.accounts[x].nature.debit_natured == (x == debit) else -1) for x in (debit, credit)}
            # the journal line number keeps two movements of one kind on one date two events
            g.post(entity, Posting(t, phase, debit, credit, amount, eff, SOURCE, f"{name}.{event}:{d.isoformat()}#{i}"))
        rec: Dict[str, Any] = {"payments": [], "collections": {}, "fees_paid": {}, "bank_interest": {},
                               "liquidity": {}, "purchases": [], "fundings": []}
        for d, kind, x in facts.get(t, []):
            if kind == "payment":
                rec["payments"].append([d.isoformat(), *x])
            elif kind in ("collection", "fee", "bank_interest", "liquidity"):
                key = {"collection": "collections", "fee": "fees_paid"}.get(kind, kind)
                rec[key][x[0]] = rec[key].get(x[0], Fraction(0)) + x[1]
            elif kind == "purchase":
                rec["purchases"].append([d.isoformat(), *x])
            elif kind == "funding":
                rec["fundings"].append([d.isoformat(), *x])
        g.record(name, t, **rec, **v.snapshots[t])

    group.register(phase, handler)


RULEBOOKS = {"vehicle": vehicle}
