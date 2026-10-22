"""Market data: observed series a contract reads but does not own (module card 3).

A rate reset reads a reference rate (``USD.SWP``, ``LIBOR_USD``); a scaled
loan reads an index (``USA.CPI``). Those numbers belong to no contract and
no book: they are observations of the world, dated, and the same series may
be read by many contracts. So they live here, keyed by their market code.

Rule for reading
----------------
A series reads under a declared rule (card 3: "never extrapolates beyond a
series without a declared rule"):

* ``exact`` (the default): the instant asked for must be an observation
  date. Anything else refuses, so a reset can never quietly price on a
  neighbour's rate. Every reset in the ACTUS corpus reads an observation
  dated on the reset itself, so the corpus runs under this rule.
* ``step``: the latest observation at or before the instant, carried
  forward without limit (a published index that holds until the next
  print). Declared by whoever loads the series.

Asking before the first observation refuses under both. Observations are
exact fractions, read from their decimal text (``"0.0105679"`` is
105679/10^7, not the nearest binary float).

The golds loan case (step 7) added this module: the ACTUS corpus ships its
reference rates as ``dataObserved`` beside each contract.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable, Mapping, Tuple

from one_engine.units import Number, exact


RULES = ("exact", "step")


class MarketError(ValueError):
    """A series is missing, empty, unordered, or read before its first observation."""


@dataclass(frozen=True)
class Series:
    """One dated series: ``points`` are ``(instant, value)`` in strictly increasing time."""

    code: str
    points: Tuple[Tuple[_dt.datetime, Fraction], ...]
    rule: str = "exact"

    @classmethod
    def of(cls, code: str, points: Iterable[Tuple[_dt.datetime, Number]], rule: str = "exact") -> "Series":
        if rule not in RULES:
            raise MarketError(f"series '{code}': reading rule {rule!r} is not built ({RULES})")
        pts = tuple((t, exact(v)) for t, v in points)
        if not pts:
            raise MarketError(f"series '{code}' has no observations")
        if any(a[0] >= b[0] for a, b in zip(pts, pts[1:])):
            raise MarketError(f"series '{code}': observation times must strictly increase")
        return cls(code, pts, rule)

    def at(self, when: _dt.datetime) -> Fraction:
        """The value at ``when`` under the series' rule (see the module docstring)."""
        if self.rule == "exact":
            found = [v for t, v in self.points if t == when]
            if not found:
                raise MarketError(f"series '{self.code}' has no observation on {when.isoformat()} "
                                  f"and reads exact dates only")
            return found[0]
        found = [v for t, v in self.points if t <= when]
        if not found:
            raise MarketError(f"series '{self.code}' has no observation at or before {when.isoformat()}")
        return found[-1]


class Market:
    """The series of one run, by code. Asking for an unknown code refuses."""

    def __init__(self, series: Mapping[str, Series]):
        self._series = dict(series)

    def series(self, code: str) -> Series:
        if code not in self._series:
            raise MarketError(f"no market series '{code}' (have {sorted(self._series)})")
        return self._series[code]
