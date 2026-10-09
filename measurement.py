"""Measurement: what a thing is worth in the books under a policy (module card 10).

Built so far: the straight-line asset register used by Averta's SPV
(capitalised construction cost, depreciated from the in-service date,
disposed of at the end of the project), and a straight-line provision
settled once (the 50MW feature case's decommissioning). Impairment,
discounting of provisions, effective-interest fees and remeasurement come
with the cases that need them.

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
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional

from one_engine.units import Number, exact


class MeasurementError(ValueError):
    """A measurement policy or register use is invalid."""


_FIRST_CHARGE = ("in_service", "next_period")


@dataclass(frozen=True)
class Policy:
    """Straight-line depreciation: life in periods and the first-charge convention."""

    life_periods: int
    first_charge: str

    def __post_init__(self):
        if not isinstance(self.life_periods, int) or self.life_periods < 1:
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
            if period < tr.in_service + lag:
                continue
            x = min(tr.cost / self.policy.life_periods, tr.cost - tr.charged)   # NBV floor
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
