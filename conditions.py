"""Conditions: how a test or trigger is evaluated (module card 8).

A :class:`Test` is rulebook data: a name, a comparison and a level. One
level is one datum, read by every caller; nothing else in the engine holds
a copy of it.

Callers supply the value to test. Conditions never reads the books.

Missing facts
-------------
A DSCR with no debt service due has no value. What that means is a decision
of the agreement, not of the code: some facilities treat it as a pass, some
as a breach. Each test therefore declares ``if_missing`` (``pass``,
``fail`` or ``unknown``); a test that meets a missing value without a
declared rule refuses rather than picking one.

Consequences (blocking tiers, reporting a breach) are attached by the
rulebook that uses the test, and the result keeps the value and level used
so every verdict can be reported.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Optional

from one_engine.units import Number, exact


class ConditionError(ValueError):
    """A test is mis-specified or meets a missing value with no rule."""


_OPS = {">=": operator.ge, ">": operator.gt, "<=": operator.le, "<": operator.lt}
_MISSING = ("pass", "fail", "unknown")


@dataclass(frozen=True)
class Test:
    """A named comparison of a supplied value against a level."""

    __test__ = False          # not a pytest class

    name: str
    op: str
    level: Number
    if_missing: Optional[str] = None

    def __post_init__(self):
        if self.op not in _OPS:
            raise ConditionError(f"test '{self.name}': comparison must be one of {sorted(_OPS)}, got '{self.op}'")
        if self.if_missing is not None and self.if_missing not in _MISSING:
            raise ConditionError(f"test '{self.name}': if_missing must be one of {_MISSING}, got '{self.if_missing}'")


@dataclass(frozen=True)
class Result:
    """A verdict with the value and level that produced it."""

    test: str
    verdict: str
    value: Optional[float]
    level: Number


def evaluate(test: Test, value: Optional[Number]) -> Result:
    """Evaluate ``test`` on ``value`` (``None`` when the fact does not exist)."""
    if value is None:
        if test.if_missing is None:
            raise ConditionError(f"test '{test.name}': the value is missing and no rule for that is declared")
        return Result(test.name, test.if_missing, None, test.level)
    ok = _OPS[test.op](exact(value), exact(test.level))    # exact: no float edge at the level
    return Result(test.name, "pass" if ok else "fail", value, test.level)
