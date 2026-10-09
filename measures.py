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
"""
from __future__ import annotations

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
    f = lambda r: npv(cfs, r)
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
