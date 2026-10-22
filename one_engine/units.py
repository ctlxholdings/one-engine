"""Exact quantities with explicit units (module card 1, Time and units).

Two decisions live here:

1. **How a number becomes exact.** The kernel holds every amount as a
   :class:`fractions.Fraction` so that its identities (two ends,
   roll-forward, balance sheet) hold with zero tolerance whatever the order
   of posting. Step 3 measured why: folding the nightmare fixture's 5,518
   postings in two orders changed 18 of 46 float ledger totals (by up to
   7.2e-7 EUR) and none of the exact ones (consolidation
   ``experiments/arith.py``). :func:`exact` is the single door.

   - A ``float`` is converted to the dyadic rational it *is*, bit for bit.
     Nothing is rounded: the float an upstream calculation produced is the
     amount the kernel records.
   - A ``str`` or ``Decimal`` is read as the decimal it names, so "0.1"
     typed by a person is one tenth, not the float nearest to it. A
     ``str`` written as a ratio, "2/15", is that ratio: a share with no
     finite decimal (the nightmare's shareholder loan funds 2/15 of each
     EPC payment) can still be typed exactly.

2. **Which units may be combined.** A :class:`Quantity` carries its unit
   code. Arithmetic or comparison across different units raises
   :class:`UnitError`; conversion (FX, tonnes to kilograms) is always an
   explicit step owned by a module, never an implicit cast here.

Out of scope for now (added when a case needs it): periods, calendars and
day counts; flow/stock flags; real or nominal bases.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from numbers import Rational
from typing import Union

Number = Union[int, float, str, Decimal, Fraction]


class UnitError(ValueError):
    """Two quantities in different units were combined without a conversion."""


def exact(value: Number) -> Fraction:
    """Return ``value`` as an exact :class:`Fraction`.

    Parameters
    ----------
    value:
        ``int``, ``Fraction`` or other rational; ``float`` (taken bit for
        bit); ``Decimal`` or decimal text such as ``"266161.51"``.

    Raises
    ------
    TypeError
        For ``bool`` (almost always a bug when used as an amount), ``None``,
        non-numeric text, NaN and infinities. None of these is an amount, and
        accepting one silently would break "nothing is silent".
    """
    if isinstance(value, bool) or value is None:
        raise TypeError(f"not an amount: {value!r}")
    if isinstance(value, Rational):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise TypeError(f"not a finite amount: {value!r}")
        return Fraction(value)            # exact: a float is m / 2**k
    if isinstance(value, str) and "/" in value:
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise TypeError(f"not an amount: {value!r}") from exc
    if isinstance(value, (Decimal, str)):
        try:
            d = Decimal(value)
        except Exception as exc:          # decimal.InvalidOperation
            raise TypeError(f"not an amount: {value!r}") from exc
        if not d.is_finite():
            raise TypeError(f"not a finite amount: {value!r}")
        return Fraction(d)
    raise TypeError(f"not an amount: {value!r} ({type(value).__name__})")


def at(value, t: int) -> Fraction:
    """A term stated as one number or as a series of one value per period, at period ``t``.

    Rulebooks read their terms this way: a tax rate, a target balance or a
    scheduled amount may be stated once or period by period. A series with
    no value for ``t`` refuses rather than repeating its last value.
    """
    if isinstance(value, (list, tuple)):
        if t >= len(value):
            raise ValueError(f"series of {len(value)} values has no period {t}")
        return exact(value[t])
    return exact(value)


@dataclass(frozen=True)
class Quantity:
    """An exact amount in one unit, such as ``Quantity("850000", "USD")``.

    Units are plain codes (ISO 4217 for currencies; ``t``, ``MWh``,
    ``head`` and similar for matter). The code is the whole identity: two
    quantities combine only if their codes are equal.
    """

    amount: Fraction
    unit: str

    def __init__(self, amount: Number, unit: str):
        if not isinstance(unit, str) or not unit:
            raise UnitError(f"a quantity needs a unit, got {unit!r}")
        object.__setattr__(self, "amount", exact(amount))
        object.__setattr__(self, "unit", unit)

    def _same(self, other: "Quantity") -> None:
        if not isinstance(other, Quantity):
            raise TypeError(f"expected a Quantity, got {type(other).__name__}")
        if other.unit != self.unit:
            raise UnitError(f"cannot combine {self.unit} with {other.unit} without a conversion")

    def __add__(self, other: "Quantity") -> "Quantity":
        self._same(other)
        return Quantity(self.amount + other.amount, self.unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        self._same(other)
        return Quantity(self.amount - other.amount, self.unit)

    def __lt__(self, other: "Quantity") -> bool:
        self._same(other)
        return self.amount < other.amount

    def __le__(self, other: "Quantity") -> bool:
        self._same(other)
        return self.amount <= other.amount
