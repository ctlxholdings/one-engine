"""Measures: streams to numbers (module card 14).

Measures read named series (from :mod:`one_engine.cuts`) and return
scalars. They never read a ledger directly and never write anything.

Refusal over a confident wrong number
-------------------------------------
* :func:`irr` refuses a stream with no sign change (no rate exists) and a
  stream for which several rates fit (:class:`AmbiguousRate`, carrying every
  root found). golds: "A founder told 'your return is 8.9%' when it is
  equally 143% has been misled by a number that looked like a fact."
* :func:`npv` has no default discount rate. An NPV at an undisclosed rate is
  refused by construction (golds' appraisal rule).
* :func:`payback` returns ``None`` when the stream never pays back, rather
  than a sentinel such as 999 (Baghara's DSCR code used 999).

Arithmetic is float: measures sit outside the exact kernel (step 3 decision).
Rates are per period of the stream; annualising is the caller's explicit step.
:func:`xirr` is the exception: its flows are dated, so its rate is annual.

Added for Bodmer's solar case (plan step 7e), whose grid mixes months and
quarters: :func:`xirr` (dated flows, actual/365, as Excel's XIRR) and
:func:`discount` (a stream times stated discount factors, for a PV at a
compounding index rather than a constant rate).

Added for the Macabacus LBO (step 7f): :func:`moic`, money out over money
in (the short-form workbook's "cash-on-cash", where nothing is paid in
after the investment).

Added for the Macabacus long-form LBO (step 7g): :func:`cash_on_cash`, the
long-form workbook's definition (every later flow, net, over the
investment), which differs from :func:`moic` when a holder pays in again.
"""
from __future__ import annotations

import datetime as _dt
from typing import List, Optional, Sequence

from one_engine.units import Number


class MeasureError(ValueError):
    """A measure is undefined for the stream given."""


class AmbiguousRate(MeasureError):
    """More than one rate sets the NPV to zero."""

    def __init__(self, roots: List[float]):
        self.roots = roots
        super().__init__(f"several rates fit this stream: {', '.join(f'{r:.6%}' for r in roots)}")


def npv(stream: Sequence[Number], rate: float) -> float:
    """Net present value at ``rate`` per period; the first flow is at t = 0.

    Evaluated by Horner's method, which stays stable on long streams (golds
    replaced a power-sum form that failed beyond 54 periods).
    """
    x = 1.0 / (1.0 + rate)
    total = 0.0
    for cf in reversed([float(c) for c in stream]):
        total = total * x + cf
    return total


def _sign_changes(values: Sequence[float]) -> int:
    signs = [v > 0 for v in values if v != 0]
    return sum(1 for a, b in zip(signs, signs[1:]) if a != b)


def _bisect(f, lo: float, hi: float, tol: float = 1e-15) -> float:
    flo = f(lo)
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = f(mid)
        if (fm > 0) == (flo > 0):
            lo, flo = mid, fm
        else:
            hi = mid
        if hi - lo < tol:
            break
    return (lo + hi) / 2


def irr(stream: Sequence[Number], lo: float = -0.99, hi: float = 10.0, grid: int = 2000) -> float:
    """Internal rate of return per period, or a refusal.

    The rate is unique when the flows change sign once (Descartes' rule of
    signs on the polynomial in 1/(1+r)). Otherwise the NPV is scanned on a
    grid over ``[lo, hi]``: one bracket gives the rate, more than one raises
    :class:`AmbiguousRate`, none raises :class:`MeasureError`.
    """
    cfs = [float(c) for c in stream]
    if _sign_changes(cfs) == 0:
        raise MeasureError("the stream never changes sign, so no rate of return exists")
    return _root(lambda r: npv(cfs, r), lo, hi, grid)


def xirr(stream: Sequence[Number], dates: Sequence[_dt.date], lo: float = -0.99, hi: float = 10.0,
         grid: int = 2000) -> float:
    """Annual rate of return of dated flows, or a refusal (Excel's XIRR convention).

    Each flow is discounted by ``(1 + r) ** (days / 365)``, days counted
    from the first date given (actual/365). The base date does not move the
    rate, so zero flows before the first payment change nothing. The same
    refusals as :func:`irr`: no sign change, several rates, none in range.
    """
    if len(dates) != len(stream):
        raise MeasureError(f"{len(stream)} flows but {len(dates)} dates")
    if any(b < a for a, b in zip(dates, dates[1:])):
        raise MeasureError("dates must not go back")
    cfs = [float(c) for c in stream]
    if _sign_changes(cfs) == 0:
        raise MeasureError("the stream never changes sign, so no rate of return exists")
    years = [(d - dates[0]).days / 365 for d in dates]
    return _root(lambda r: sum(c / (1 + r) ** y for c, y in zip(cfs, years) if c), lo, hi, grid)


def discount(stream: Sequence[Number], factors: Sequence[Number]) -> float:
    """Present value of a stream at stated discount factors, one per period.

    For a PV at a compounding index (the lender's rate index of a sculpted
    loan): the factor is ``1 / index``. Lengths must agree: a factor the
    caller forgot would otherwise discount at zero.
    """
    if len(factors) != len(stream):
        raise MeasureError(f"{len(stream)} flows but {len(factors)} discount factors")
    return sum(float(c) * float(f) for c, f in zip(stream, factors))


def _root(f, lo: float, hi: float, grid: int) -> float:
    """The one rate in ``[lo, hi]`` where ``f`` is zero, or a refusal (grid scan, then bisection)."""
    xs = [lo + (hi - lo) * i / grid for i in range(grid + 1)]
    roots = []
    prev_x, prev_f = xs[0], f(xs[0])
    for x in xs[1:]:
        fx = f(x)
        if fx == 0:
            roots.append(x)
        elif (fx > 0) != (prev_f > 0) and prev_f != 0:
            roots.append(_bisect(f, prev_x, x))
        prev_x, prev_f = x, fx
    if not roots:
        raise MeasureError(f"no rate between {lo:.0%} and {hi:.0%} sets the NPV to zero")
    if len(roots) > 1:
        raise AmbiguousRate(roots)
    return roots[0]


def moic(stream: Sequence[Number]) -> float:
    """Multiple on invested capital: what the holder received over what it paid.

    The sum of the positive flows over the sum of the negative ones, in
    absolute value. Refuses a stream with no outflow (nothing was invested).
    """
    paid = -sum(float(c) for c in stream if c < 0)
    if paid == 0:
        raise MeasureError("the stream has no outflow, so nothing was invested")
    return sum(float(c) for c in stream if c > 0) / paid


def cash_on_cash(stream: Sequence[Number]) -> float:
    """Cash-on-cash multiple: every flow after the first, net, over the first (the investment).

    Macabacus's definition (long-form LBO, step 7g). It equals :func:`moic`
    when nothing is paid in after the first period; when the holder pays
    in again later (a preferred holder funding a coupon shortfall), that
    payment lowers the multiple here instead of adding to what was
    invested. Refuses a stream that does not start with an outflow.
    """
    if not stream or stream[0] >= 0:
        raise MeasureError("the stream does not start with an investment (a negative first flow)")
    return -sum(float(c) for c in stream[1:]) / float(stream[0])


def coverage(available: Number, service: Number):
    """A coverage ratio (DSCR-style), exact; ``None`` when nothing is due.

    ``None`` rather than infinity or a sentinel such as 999: what a missing
    ratio means for a covenant is declared by the test (Conditions).
    """
    from one_engine.units import exact
    service = exact(service)
    return None if service == 0 else exact(available) / service


def payback(stream: Sequence[Number]) -> Optional[float]:
    """Simple (undiscounted) payback in periods, interpolated within a period.

    Returns ``None`` if cumulative cash never turns non-negative after the
    initial outflow.
    """
    cum = 0.0
    for t, cf in enumerate(float(c) for c in stream):
        before = cum
        cum += cf
        if t > 0 and before < 0 <= cum:
            return (t - 1) + (-before / cf)
    return None
