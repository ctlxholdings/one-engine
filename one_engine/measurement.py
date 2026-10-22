"""Measurement: what a thing is worth in the books under a policy (module card 10).

Built so far: the straight-line asset register used by Averta's SPV
(capitalised construction cost, depreciated from the in-service date,
disposed of at the end of the project), and a straight-line provision
settled once (the 50MW feature case's decommissioning), and the
remeasurement of a foreign-currency monetary balance at the closing rate
(the nightmare case's dollar loan), weighted straight-line and declining
balance (ModelOff, steps 7h and 7i). Impairment, discounting of provisions
and effective-interest fees come with the cases that need them.

The first-charge convention is declared, never defaulted
-------------------------------------------------------
When does the first depreciation charge fall: in the period the asset goes
into service, or the period after? Both are used. The old engine charges
from the period after COD, so Averta's 100MW benchmark depreciates 96 of
its 100 quarters of life and writes off 4M at the end (step 6 finding).
:class:`Policy` therefore requires ``first_charge`` and refuses an empty
value.

The register keeps the net book value floor (card 10 "Owns"): a tranche is
never depreciated below zero. Amounts are exact.

Weighted straight-line (ModelOff 2015 "More Money Please", step 7h)
-------------------------------------------------------------------
Instead of a life in periods, a policy may give ``weights``, one per
period (the days of operation in each year): a tranche is charged, each
period from its first charge, its cost times that period's weight over the
weights left from its first charge to the end of the series. Its cost is
therefore spread over the rest of the series in proportion to the weights,
and the last period with a weight takes the last of it. This is the
"depreciate what is left over the days of operation left" pool the
ModelOff case uses: since that rule is linear in the pool, the pool's
charge equals the sum of its tranches' (tested).

Declining balance (ModelOff 2012 "Asset Schedule", step 7i)
-----------------------------------------------------------
A policy may instead give a ``declining_rate``: each period from its first
charge, a tranche is charged that share of its net book value (cost less
what has been charged). Its NBV after ``k`` charges is therefore
``cost x (1 - rate) ** k``; it never reaches zero on its own, so whatever
is left goes at disposal. Tax authorities use it (the case's 40% tax
depreciation); a rate of one charges the whole cost at once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional, Sequence

from one_engine.units import Number, exact


class MeasurementError(ValueError):
    """A measurement policy or register use is invalid."""


_FIRST_CHARGE = ("in_service", "next_period")


@dataclass(frozen=True)
class Policy:
    """Depreciation: a life in periods, weights by period, or a declining rate; and the first-charge convention.

    Exactly one of ``life_periods`` (equal charges), ``weights`` (a
    series: each period's share of what is left) and ``declining_rate``
    (a share of each tranche's net book value every period) is given; see
    the module docstring.
    """

    life_periods: Optional[int]
    first_charge: str
    weights: Optional[Sequence[Number]] = None
    declining_rate: Optional[Number] = None

    def __post_init__(self):
        given = [x is not None for x in (self.life_periods, self.weights, self.declining_rate)]
        if sum(given) != 1:
            raise MeasurementError("a policy takes a life in periods, weights by period or a declining rate, exactly one")
        if self.declining_rate is not None:
            rate = exact(self.declining_rate)
            if not 0 < rate <= 1:
                raise MeasurementError(f"declining_rate must be above 0 and at most 1, got {rate}")
            object.__setattr__(self, "declining_rate", rate)
        elif self.weights is not None:
            w = tuple(exact(x) for x in self.weights)
            if any(x < 0 for x in w):
                raise MeasurementError(f"weights must not be negative, got {list(self.weights)}")
            object.__setattr__(self, "weights", w)
        elif not isinstance(self.life_periods, int) or self.life_periods < 1:
            raise MeasurementError(f"life_periods must be a positive integer, got {self.life_periods!r}")
        if self.first_charge not in _FIRST_CHARGE:
            raise MeasurementError(f"first_charge must be declared as one of {_FIRST_CHARGE}, got {self.first_charge!r}")


@dataclass
class _Tranche:
    in_service: int
    cost: Fraction
    charged: Fraction = Fraction(0)


class Register:
    """Tranches of capitalised cost and their depreciation under one policy.

    Usage::

        reg = Register(Policy(life_periods=100, first_charge="in_service"))
        reg.add(period=4, cost=cwip_balance)     # at COD
        dep = reg.charge(t)                      # once per period, in order
        loss = reg.dispose()                     # remaining NBV at the end
    """

    def __init__(self, policy: Policy):
        self.policy = policy
        self._tranches: List[_Tranche] = []
        self._last_charged: Optional[int] = None

    def add(self, period: int, cost: Number) -> None:
        """A tranche goes into service in ``period`` at ``cost``."""
        cost = exact(cost)
        if period < 0 or cost < 0:
            raise MeasurementError(f"a tranche needs a period >= 0 and a cost >= 0, got {period}, {cost}")
        self._tranches.append(_Tranche(period, cost))

    def charge(self, period: int) -> Fraction:
        """Depreciation for ``period``; each period is charged at most once, in order."""
        if self._last_charged is not None and period <= self._last_charged:
            raise MeasurementError(f"period {period} is charged after period {self._last_charged}")
        self._last_charged = period
        lag = 0 if self.policy.first_charge == "in_service" else 1
        total = Fraction(0)
        for tr in self._tranches:
            first = tr.in_service + lag
            if period < first:
                continue
            if self.policy.declining_rate is not None:
                step = (tr.cost - tr.charged) * self.policy.declining_rate
            elif self.policy.weights is None:
                step = tr.cost / self.policy.life_periods
            else:
                w = self.policy.weights
                left = sum(w[first:], Fraction(0))
                if left == 0:
                    if tr.cost:
                        raise MeasurementError(f"a tranche of {tr.cost} from period {first} has no weight left to charge")
                    continue
                step = tr.cost * (w[period] if period < len(w) else 0) / left
            x = min(step, tr.cost - tr.charged)                                  # NBV floor
            tr.charged += x
            total += x
        return total

    def nbv(self) -> Fraction:
        return sum((tr.cost - tr.charged for tr in self._tranches), Fraction(0))

    def dispose(self) -> Fraction:
        """Write off every tranche; returns the net book value removed."""
        out = self.nbv()
        for tr in self._tranches:
            tr.charged = tr.cost
        return out


class Provision:
    """A liability built up straight-line and settled once.

    ``amount`` is charged in equal parts of ``amount / periods`` from
    ``start``; in ``settle_at`` whatever has not been charged yet is
    charged (the true-up) and the whole amount is paid. Charging stops
    once the amount is reached, so the provision never exceeds it.
    Undiscounted: no case so far states a discount rate for it.
    """

    def __init__(self, amount: Number, start: int, periods: int, settle_at: int):
        self.amount = exact(amount)
        if self.amount < 0 or periods < 1 or settle_at < start:
            raise MeasurementError(f"provision needs amount >= 0, periods >= 1, settle_at >= start; "
                                   f"got {self.amount}, {periods}, {start}..{settle_at}")
        self.start, self.periods, self.settle_at = start, periods, settle_at
        self.charged = Fraction(0)

    def charge(self, period: int) -> Fraction:
        """The charge for ``period`` (the true-up in ``settle_at``)."""
        if period == self.settle_at:
            x = self.amount - self.charged
        elif self.start <= period < self.settle_at:
            x = min(self.amount / self.periods, self.amount - self.charged)
        else:
            x = Fraction(0)
        self.charged += x
        return x

    def payment(self, period: int) -> Fraction:
        return self.amount if period == self.settle_at else Fraction(0)


def remeasure(foreign: Number, rate: Number, carrying: Number) -> Fraction:
    """The adjustment that brings a foreign-currency monetary balance to the closing rate.

    A monetary item (a loan, a payable) in a foreign currency is carried at
    ``foreign x rate`` at each close, the difference going to profit or
    loss (IAS 21.23 and 21.28). Returns ``foreign x rate - carrying``:
    positive raises the carrying amount (a loss on a liability), negative
    lowers it. ``rate`` is functional units per foreign unit.
    """
    rate = exact(rate)
    if rate <= 0:
        raise MeasurementError(f"an exchange rate must be positive, got {rate}")
    return exact(foreign) * rate - exact(carrying)
