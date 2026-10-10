"""Dates: dated clocks, payment cycles, business days and day counts (module card 1, Time and units).

Why a shared module
-------------------
Until the golds loan case every assembly ran on a grid of equal periods
(quarters) and priced interest with one fixed fraction per period
(91.25 / 360). A loan contract states dates instead: an anchor, a cycle
("every month, long last stub"), a maturity, a business-day rule and a day
count. The ACTUS corpus (``cases/reference/actus``) prices every coupon
from those dates, so the dates have to be derived, not typed in.

What it holds, and only that
----------------------------
* :func:`instant` parses an ISO date or date-time. A time of ``23:59:59``
  is kept: such an instant is after every midnight of its own day, is
  never itself a cycle date, and prices as the following midnight
  (:func:`pricing_date`). Any other time of day refuses: the corpus
  evidences one sub-day rule only (pam25, ann19, lam18, as found by golds).
* :class:`Cycle` parses ACTUS's ``P<n><unit>L<s>``. The trailing flag is
  the rule for an odd last interval: ``L0`` merges it into the period
  before (one long last period), ``L1`` keeps it (a short last period).
* :func:`schedule` walks a cycle from an anchor to an end date, with the
  end-of-month rule: under ``EOM`` an anchor on a month's last day lands on
  every later month's last day; under ``SD`` (same day) the day is kept and
  clamped to the month's length.
* :func:`shift` applies a business-day convention under a calendar (``NC``
  no holidays, ``MF`` Saturdays and Sundays, plus any holidays listed as
  data: :func:`is_business_day`). The first two letters decide
  which date the interest is computed on: ``SC`` (shift, then calculate)
  accrues to the shifted date, ``CS`` (calculate, then shift) to the
  unshifted one while only the payment moves.
* :func:`year_fraction` for the four conventions the corpus uses (A365,
  A360, AA = actual/actual ISDA, 30E360). Exact fractions, like the kernel.
* :func:`nth_weekday` and :func:`nth_business_day` name a day of a month
  by its rank: the third Thursday, the first business day (ModelOff 2017
  "Collect the Cash", step 7q, whose customers pay on such days; neither
  is a cycle step from an anchor).

Everything unbuilt refuses by name; a near-miss convention is never
borrowed, because a wrong day count is a wrong interest bill that ties.

This module knows nothing about contracts; packs read it.
"""
from __future__ import annotations

import datetime as _dt
import re
from dataclasses import dataclass
from fractions import Fraction
from typing import FrozenSet, List, Optional, Tuple


class CalendarError(ValueError):
    """A date, cycle or convention cannot be read or is not built."""


DAY_COUNTS = ("A365", "A360", "AA", "30E360")
CALENDARS = ("NC", "MF")
BUSINESS_DAY = ("NOS", "SCF", "SCMF", "CSF", "CSMF", "SCP", "SCMP", "CSP", "CSMP")
END_OF_MONTH = ("SD", "EOM")

_EOD = _dt.time(23, 59, 59)


def instant(text: str) -> _dt.datetime:
    """An ISO date or date-time as a datetime; only midnight and 23:59:59 are accepted."""
    s = str(text).strip()
    try:
        if "T" not in s:
            return _dt.datetime.combine(_dt.date.fromisoformat(s), _dt.time(0))
        day, clock = s.split("T", 1)
        t = _dt.time.fromisoformat(clock)
        d = _dt.date.fromisoformat(day)
    except ValueError as e:
        raise CalendarError(f"{text!r} is not an ISO date or date-time ({e})") from None
    if t not in (_dt.time(0), _EOD):
        raise CalendarError(f"{text!r}: a time of day other than midnight or 23:59:59 has no rule here")
    return _dt.datetime.combine(d, t)


def pricing_date(x: _dt.datetime) -> _dt.date:
    """The date a day count reads: the day itself, or the next midnight for 23:59:59."""
    return x.date() + _dt.timedelta(days=1) if x.time() == _EOD else x.date()


# ── cycles ──────────────────────────────────────────────────────────────────

_CYCLE = re.compile(r"^P(\d+)([DWMQHY])L([01])$")


@dataclass(frozen=True)
class Cycle:
    """A payment cycle: every ``n`` units, with a long (L0) or short (L1) last stub."""

    n: int
    unit: str
    long_stub: bool

    @classmethod
    def parse(cls, text: str) -> "Cycle":
        m = _CYCLE.match(str(text).strip())
        if not m:
            raise CalendarError(f"cycle {text!r} is not of the form P<n><D|W|M|Q|H|Y>L<0|1>")
        n, unit, stub = int(m.group(1)), m.group(2), m.group(3)
        if n <= 0:
            raise CalendarError(f"cycle {text!r}: the step must be positive")
        return cls(n, unit, stub == "0")

    @property
    def months(self) -> Optional[int]:
        """The step in months, or None for a step counted in days or weeks."""
        return {"M": 1, "Q": 3, "H": 6, "Y": 12}.get(self.unit, 0) * self.n or None

    def step(self, anchor: _dt.datetime, k: int, eom: bool) -> _dt.datetime:
        """The anchor moved ``k`` steps. ``eom``: the anchor is a month end and EOM applies."""
        if self.months is None:
            days = self.n * k * (7 if self.unit == "W" else 1)
            return anchor + _dt.timedelta(days=days)
        total = anchor.month - 1 + self.months * k
        y, m = anchor.year + total // 12, total % 12 + 1
        day = _last_day(y, m) if eom else min(anchor.day, _last_day(y, m))
        return _dt.datetime.combine(_dt.date(y, m, day), anchor.time())


def _is_leap(y: int) -> bool:
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def _last_day(y: int, m: int) -> int:
    return [31, 29 if _is_leap(y) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]


def schedule(anchor: _dt.datetime, cycle: Optional[str], end: _dt.datetime,
             end_of_month: str = "SD", include_end: bool = True) -> List[_dt.datetime]:
    """ACTUS's S(anchor, cycle, end): the cycle dates from ``anchor`` up to ``end``.

    Dates are ``anchor + k * cycle`` while before ``end``. If the last of them
    is not ``end`` itself, a long stub (``L0``) drops it, so its period merges
    into the final one; a short stub (``L1``) keeps it. ``end`` closes the
    list when ``include_end``. With no cycle the schedule is the anchor and
    the end. The anchor itself is never dropped by the stub rule.
    """
    if end_of_month not in END_OF_MONTH:
        raise CalendarError(f"end-of-month convention {end_of_month!r} is not built ({END_OF_MONTH})")
    if cycle is None:
        out = [anchor] if anchor < end else []
        return out + ([end] if include_end else [])
    c = Cycle.parse(cycle)
    eom = (end_of_month == "EOM" and c.months is not None
           and anchor.day == _last_day(anchor.year, anchor.month))
    out, k = [], 0
    while c.step(anchor, k, eom) < end:
        out.append(c.step(anchor, k, eom))
        k += 1
    on_cycle = c.step(anchor, k, eom) == end
    if not on_cycle and c.long_stub and len(out) > 1:
        out.pop()                                    # L0: the odd piece merges into the period before
    return out + ([end] if include_end else [])


# ── business days ───────────────────────────────────────────────────────────

def is_business_day(d: _dt.datetime, calendar: Optional[str], holidays: FrozenSet[_dt.date] = frozenset()) -> bool:
    """Whether ``d`` is a working day: every day under ``NC``; Monday to Friday, less ``holidays``, under ``MF``."""
    calendar = calendar or "NC"
    if calendar not in CALENDARS:
        raise CalendarError(f"calendar {calendar!r} is not built ({CALENDARS}); give its holidays as a list")
    if calendar == "NC":
        if holidays:
            raise CalendarError("a holiday list needs the weekday calendar (MF); NC has no non-working days")
        return True
    day = d.date() if isinstance(d, _dt.datetime) else d
    return day.weekday() < 5 and day not in holidays


def shift(d: _dt.datetime, convention: Optional[str], calendar: Optional[str],
          holidays: FrozenSet[_dt.date] = frozenset()) -> Tuple[_dt.datetime, _dt.datetime]:
    """``(payment instant, calculation instant)`` of a scheduled instant.

    Following (``F``) moves to the next working day, preceding (``P``) to
    the previous one; the modified forms reverse direction if the move
    would leave the month. ``SC`` computes on the shifted day, ``CS`` on
    the scheduled one. ``holidays`` (dates) are non-working days of the
    weekday calendar ``MF`` besides the weekend (ModelOff 2014 "Precise
    Debt", step 7m, states eighteen).
    """
    convention = convention or "NOS"
    if convention not in BUSINESS_DAY:
        raise CalendarError(f"business-day convention {convention!r} is not built ({BUSINESS_DAY})")
    is_business_day(d, calendar, holidays)                  # validates the calendar and the list
    if convention == "NOS" or (calendar or "NC") == "NC":
        return d, d
    forward = convention.endswith("F")
    step = _dt.timedelta(days=1 if forward else -1)
    out = d
    while not is_business_day(out, calendar, holidays):
        out += step
    if "M" in convention[2:] and out.month != d.month:
        out = d
        while not is_business_day(out, calendar, holidays):
            out -= step
    return out, (out if convention.startswith("SC") else d)


def nth_weekday(year: int, month: int, weekday: int, n: int) -> _dt.datetime:
    """The ``n``-th ``weekday`` (0 Monday .. 6 Sunday) of a month, counting from one: the third Thursday.

    A month has four or five of each weekday; asking for one it does not
    have refuses rather than spilling into the next month.
    """
    if not 0 <= weekday <= 6 or n < 1:
        raise CalendarError(f"weekday {weekday} (0..6) and rank {n} (from 1) do not name a day")
    first = _dt.date(year, month, 1)
    day = 1 + (weekday - first.weekday()) % 7 + 7 * (n - 1)
    if day > _last_day(year, month):
        raise CalendarError(f"{year}-{month:02d} has no weekday {weekday} number {n}")
    return _dt.datetime(year, month, day)


def nth_business_day(year: int, month: int, n: int, calendar: Optional[str],
                     holidays: FrozenSet[_dt.date] = frozenset()) -> _dt.datetime:
    """The ``n``-th working day of a month under ``calendar`` (see :func:`is_business_day`), counting from one."""
    if n < 1:
        raise CalendarError(f"rank {n} does not name a day: the first business day is 1")
    count = 0
    for day in range(1, _last_day(year, month) + 1):
        d = _dt.datetime(year, month, day)
        if is_business_day(d, calendar, holidays):
            count += 1
            if count == n:
                return d
    raise CalendarError(f"{year}-{month:02d} has fewer than {n} business days")


# ── day counts ──────────────────────────────────────────────────────────────

def year_fraction(convention: str, start: _dt.datetime, end: _dt.datetime) -> Fraction:
    """Y(start, end) under ``convention``, exact; zero when ``end`` is not after ``start``."""
    if convention not in DAY_COUNTS:
        raise CalendarError(f"day count {convention!r} is not built ({DAY_COUNTS})")
    a, b = pricing_date(start), pricing_date(end)
    if b <= a:
        return Fraction(0)
    if convention == "A365":
        return Fraction((b - a).days, 365)
    if convention == "A360":
        return Fraction((b - a).days, 360)
    if convention == "AA":
        total, cur = Fraction(0), a
        while cur < b:
            stop = min(_dt.date(cur.year + 1, 1, 1), b)
            total += Fraction((stop - cur).days, 366 if _is_leap(cur.year) else 365)
            cur = stop
        return total
    d1, d2 = min(a.day, 30), min(b.day, 30)
    return Fraction(360 * (b.year - a.year) + 30 * (b.month - a.month) + (d2 - d1), 360)
