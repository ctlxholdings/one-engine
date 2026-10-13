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

Added for ModelOff 2014 "Time is Money" (step 7p): :func:`xnpv`, the NPV of
dated flows at an annual rate (Excel's XNPV, actual/365 from the first
date), beside :func:`npv`, whose flows are one period apart.

Changed for the numpy-financial and pyxirr test suites (oracles/library.py):
the rate scan reaches from a hair above -100% to about +298,000% (both
libraries publish rates the old -99%..+1000% scan refused); dated flows
after the first may come in any order, as Excel and pyxirr allow, and only a
date before the first is refused; npv refuses a rate of -100% or below
(numpy-financial answers NaN; the engine divided by zero).

Added for the same two suites and for Baghara's cattle workbook (about
1,200 IPMT/PPMT cells): the annuity functions :func:`pv`, :func:`fv`,
:func:`pmt`, :func:`ipmt`, :func:`ppmt`, :func:`nper`, :func:`rate`, and
:func:`mirr`. They follow Excel and numpy-financial: money received is
positive and money paid negative, so a loan of +1,000 has payments below
zero; ``when`` is 0 for payments at the end of each period (in arrears) and
1 for payments at the start (in advance), Excel's ``type``. pv, fv, pmt,
ipmt and ppmt are closed forms in plain arithmetic, so exact inputs
(fractions, a whole number of periods) give exact answers and the identities
between them hold to the unit; nper takes a logarithm and rate is a root, so
both are floats. Where the libraries answer NaN, the engine refuses: a
period outside the term, a term of no payments, a rate at or below -100%, a
loan the payments never repay, no rate that repays it.
"""
from __future__ import annotations

import datetime as _dt
import math
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
    replaced a power-sum form that failed beyond 54 periods). A rate of
    -100% or below discounts nothing to anything, so it is refused
    (numpy-financial answers NaN).
    """
    if rate <= -1:
        raise MeasureError(f"a rate of {rate:.0%} is at or below -100%, where discounting is undefined")
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


#: The rates the root scan covers, as growth factors 1 + r: from e**-30 (a rate a hair above -100%)
#: to e**8 (about +298,000%). numpy-financial and pyxirr publish rates at -99.9999999% and +3,290%,
#: which a scan stopping at -99% and +1000% refused as "no rate" (oracles/library.py).
LOW_GROWTH, HIGH_GROWTH = math.exp(-30), math.exp(8)


def irr(stream: Sequence[Number], grid: int = 2000) -> float:
    """Internal rate of return per period, or a refusal.

    The rate is unique when the flows change sign once (Descartes' rule of
    signs on the polynomial in 1/(1+r)). Otherwise the NPV is scanned (see
    :func:`_root`): one bracket gives the rate, more than one raises
    :class:`AmbiguousRate`, none raises :class:`MeasureError`.
    """
    cfs = [float(c) for c in stream]
    if _sign_changes(cfs) == 0:
        raise MeasureError("the stream never changes sign, so no rate of return exists")
    return _root(lambda r: _signed_pv(cfs, r), grid)


def _signed_pv(cfs: Sequence[float], r: float) -> float:
    """The NPV at ``r`` times a positive factor: same sign and roots, and no overflow.

    At r >= 0, Horner in 1/(1+r) as :func:`npv` (each step shrinks). Below
    0 the discount factor 1/(1+r) grows past what a float holds over a long
    stream, so the NPV times (1+r)**n is evaluated instead, by Horner in
    1+r from the first flow, which shrinks the same way.
    """
    if r >= 0:
        return npv(cfs, r)
    g, total = 1.0 + r, 0.0
    for cf in cfs:
        total = total * g + cf
    return total


def xirr(stream: Sequence[Number], dates: Sequence[_dt.date], grid: int = 2000) -> float:
    """Annual rate of return of dated flows, or a refusal (Excel's XIRR convention).

    Each flow is discounted by ``(1 + r) ** (days / 365)``, days counted
    from the first date given (actual/365). The base date does not move the
    rate, so zero flows before the first payment change nothing. As in
    Excel, the later dates may come in any order, but none may precede the
    first. The same refusals as :func:`irr`: no sign change, several rates,
    none in range.
    """
    years = _years(stream, dates)
    cfs = [float(c) for c in stream]
    if _sign_changes(cfs) == 0:
        raise MeasureError("the stream never changes sign, so no rate of return exists")
    span = max(years, default=0.0)

    def signed(r: float) -> float:
        # the XNPV times (1 + r) ** span when r < 0: every exponent is then <= 0, so no term
        # overflows (Python's float ** raises on overflow rather than returning inf)
        u = math.log1p(r)
        ref = span if u < 0 else 0.0
        return sum(c * math.exp((ref - y) * u) for c, y in zip(cfs, years) if c)

    return _root(signed, grid)


def _years(stream: Sequence[Number], dates: Sequence[_dt.date]) -> List[float]:
    """Each flow's time from the first date given, in years of 365 days; refuses a date before it."""
    if len(dates) != len(stream):
        raise MeasureError(f"{len(stream)} flows but {len(dates)} dates")
    if any(d < dates[0] for d in dates):
        raise MeasureError("a date comes before the first date given, which is the valuation date")
    return [(d - dates[0]).days / 365 for d in dates]


def xnpv(stream: Sequence[Number], dates: Sequence[_dt.date], rate: float) -> float:
    """Net present value of dated flows at an annual ``rate`` (Excel's XNPV convention).

    Each flow is discounted by ``(1 + rate) ** (days / 365)``, days counted
    from the first date given (actual/365), which is the valuation date. A
    leap year therefore discounts a year-end flow by 366/365 of a year,
    which is what separates XNPV from :func:`npv` on annual flows. Later
    dates may come in any order; none may precede the first (Excel's rule).
    """
    return sum(float(c) / (1 + rate) ** y for c, y in zip(stream, _years(stream, dates)))


def discount(stream: Sequence[Number], factors: Sequence[Number]) -> float:
    """Present value of a stream at stated discount factors, one per period.

    For a PV at a compounding index (the lender's rate index of a sculpted
    loan): the factor is ``1 / index``. Lengths must agree: a factor the
    caller forgot would otherwise discount at zero.
    """
    if len(factors) != len(stream):
        raise MeasureError(f"{len(stream)} flows but {len(factors)} discount factors")
    return sum(float(c) * float(f) for c, f in zip(stream, factors))


def _root(f, grid: int) -> float:
    """The one rate where ``f`` is zero, or a refusal (grid scan, then bisection).

    The grid is the old one, ``grid`` steps from -99% to +1000%, with a tail
    on each side spaced evenly in log(1 + r): 500 steps down to
    ``LOW_GROWTH`` and 500 up to ``HIGH_GROWTH``. The middle keeps its fine
    steps, where two close rates are likeliest; the tails find a rate near
    -100% or in the thousands of percent that the old scan refused.
    """
    lo_g, hi_g, tail = 0.01, 11.0, 500
    below = [math.expm1(math.log(LOW_GROWTH) + (math.log(lo_g) - math.log(LOW_GROWTH)) * i / tail)
             for i in range(tail)]
    middle = [(lo_g - 1) + (hi_g - lo_g) * i / grid for i in range(grid + 1)]
    above = [math.expm1(math.log(hi_g) + (math.log(HIGH_GROWTH) - math.log(hi_g)) * i / tail)
             for i in range(1, tail + 1)]
    xs = below + middle + above
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
        raise MeasureError(f"no rate from {xs[0]:.10%} to {xs[-1]:,.0%} sets the NPV to zero")
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


# ── annuities (numpy-financial, pyxirr, Baghara's IPMT/PPMT) ────────────────

def _check(rate: Number, when: int) -> None:
    if when not in (0, 1):
        raise MeasureError(f"when is 0 (paid at the end of each period) or 1 (at the start), not {when!r}")
    if rate <= -1:
        raise MeasureError(f"a rate of {float(rate):.0%} is at or below -100%, where discounting is undefined")


def _annuity(rate: Number, nper: Number, when: int) -> Number:
    """What a payment of 1 a period is worth at the end of the term: ``nper`` at a zero rate."""
    if rate == 0:
        return nper
    return (1 + rate * when) * ((1 + rate) ** nper - 1) / rate


def fv(rate: Number, nper: Number, pmt: Number, pv: Number, when: int = 0) -> Number:
    """Future value: what is left after ``nper`` periods of ``pmt`` on a starting ``pv`` (Excel's FV).

    The balance grows at ``rate`` and each payment adds to it; the answer
    is the amount that would bring it to nothing, so a loan received (+)
    and repaid in full gives 0, and one part repaid gives a negative number.
    """
    _check(rate, when)
    return -(pv * (1 + rate) ** nper + pmt * _annuity(rate, nper, when))


def pv(rate: Number, nper: Number, pmt: Number, fv: Number = 0, when: int = 0) -> Number:
    """Present value of ``nper`` payments of ``pmt`` and a final ``fv`` (Excel's PV)."""
    _check(rate, when)
    return -(fv + pmt * _annuity(rate, nper, when)) / (1 + rate) ** nper


def pmt(rate: Number, nper: Number, pv: Number, fv: Number = 0, when: int = 0) -> Number:
    """The level payment that takes ``pv`` to ``-fv`` over ``nper`` periods (Excel's PMT)."""
    _check(rate, when)
    if nper == 0:
        raise MeasureError("a term of 0 periods has no payments to size")
    return -(fv + pv * (1 + rate) ** nper) / _annuity(rate, nper, when)


def ipmt(rate: Number, per: Number, nper: Number, pv: Number, fv: Number = 0, when: int = 0) -> Number:
    """The interest part of payment ``per`` (1 to ``nper``) of the level payment (Excel's IPMT).

    The interest on what is still owed after ``per - 1`` payments. Paid in
    advance, the first payment falls before any interest has run (0), and
    each later one pays the interest of the period before it, discounted
    one period, as Excel and both libraries do.

    What is owed after ``k`` payments is ``(pv (g**n - g**k) - fv (g**k -
    1)) / (g**n - 1)`` with ``g = 1 + rate`` (the payment, ``when`` and all,
    cancels out). Taking it as :func:`fv` of the first ``k`` payments, as
    numpy-financial does, subtracts two numbers near ``pv g**k``: at 14.79%
    over 300 periods, period 297's interest came out -2,423 where it is
    16.97 (numpy-financial issue 35; pyxirr's test of it is an oracle).
    Here the powers are taken so they shrink: over ``g**n`` when ``g`` > 1.
    """
    _check(rate, when)
    if not 1 <= per <= nper:
        raise MeasureError(f"period {per} is outside the term: payments are periods 1 to {nper}")
    if when == 1 and per == 1:
        return 0 * rate
    k = per - 1
    if rate == 0:
        owed = pv - (pv + fv) * k / nper
    elif rate > 0:
        d, e = (1 + rate) ** (k - nper), (1 + rate) ** -nper
        owed = (pv * (1 - d) - fv * (d - e)) / (1 - e)
    else:
        gk, gn = (1 + rate) ** k, (1 + rate) ** nper
        owed = (pv * (gn - gk) - fv * (gk - 1)) / (gn - 1)
    interest = -owed * rate
    return interest / (1 + rate) if when == 1 else interest


def ppmt(rate: Number, per: Number, nper: Number, pv: Number, fv: Number = 0, when: int = 0) -> Number:
    """The principal part of payment ``per``: the level payment less its interest (Excel's PPMT)."""
    return pmt(rate, nper, pv, fv, when) - ipmt(rate, per, nper, pv, fv, when)


def nper(rate: Number, pmt: Number, pv: Number, fv: Number = 0, when: int = 0) -> float:
    """The number of periods of ``pmt`` that take ``pv`` to ``-fv`` (Excel's NPER), as a float.

    Infinite when the payment is nil at a zero rate (the balance never
    moves). Refused when the payments never get there: interest outruns
    them, or they move the balance away from the target (Excel and
    numpy-financial answer a negative number of periods, or NaN).
    """
    _check(rate, when)
    if rate == 0:
        if pmt == 0:
            if fv + pv == 0:
                raise MeasureError("the balance is already at the target, and no payment moves it")
            return math.inf
        n = -float(fv + pv) / float(pmt)
    else:
        # (1 + r)**n = (z - fv) / (pv + z), with z the payment's worth as a perpetuity
        z = float(pmt) * (1 + float(rate) * when) / float(rate)
        a = (z - float(fv)) / (float(pv) + z) if float(pv) + z else -1.0
        if a <= 0:
            raise MeasureError("the payments never repay the balance: interest outruns them")
        n = math.log(a) / math.log1p(float(rate))
    if n < 0:
        raise MeasureError("the payments never repay the balance: they move it away from the target")
    return n


def rate(nper: Number, pmt: Number, pv: Number, fv: Number = 0, when: int = 0, grid: int = 2000) -> float:
    """The rate per period at which ``nper`` payments of ``pmt`` take ``pv`` to ``-fv`` (Excel's RATE).

    The root of :func:`fv` in the rate, found by the same scan as
    :func:`irr` (one root, or a refusal naming them all); the libraries run
    Newton's method from a guess and answer NaN when it does not settle.
    Each side of zero is evaluated in a form that cannot overflow: below
    0, ``fv`` as it stands (``(1 + r)**n`` shrinks); above, ``fv`` over
    ``(1 + r)**n``.
    """
    if when not in (0, 1):
        raise MeasureError(f"when is 0 (paid at the end of each period) or 1 (at the start), not {when!r}")
    n, p, v, f = (float(x) for x in (nper, pmt, pv, fv))

    def signed(r: float) -> float:
        u = math.log1p(r)
        per_payment = (1 + r * when) * (n if r == 0 else math.expm1(n * u) / r)
        if r <= 0:
            return v * math.exp(n * u) + p * per_payment + f
        return v + p * per_payment * math.exp(-n * u) + f * math.exp(-n * u)

    try:
        return _root(signed, grid)
    except AmbiguousRate:
        raise
    except MeasureError:
        raise MeasureError(f"no rate repays {v:g} by {nper} payments of {p:g} to a final {f:g}") from None


def mirr(stream: Sequence[Number], finance_rate: float, reinvest_rate: float) -> float:
    """Modified internal rate of return (Excel's MIRR), one period apart.

    The outflows are financed at ``finance_rate`` (brought to t = 0) and
    the inflows reinvested at ``reinvest_rate`` (carried to the last
    period); the rate is the one that grows the first into the second.
    Refused unless the stream has both an outflow and an inflow.
    """
    cfs = [float(c) for c in stream]
    if not (any(c < 0 for c in cfs) and any(c > 0 for c in cfs)):
        raise MeasureError("the stream needs both an outflow and an inflow for a modified rate of return")
    n = len(cfs)
    grown = abs(npv([c if c > 0 else 0.0 for c in cfs], reinvest_rate))
    financed = abs(npv([c if c < 0 else 0.0 for c in cfs], finance_rate))
    return (grown / financed) ** (1 / (n - 1)) * (1 + reinvest_rate) - 1
