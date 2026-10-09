"""Allocation: who gets what, and in which order (module card 7).

One operator serves every rulebook that divides an amount: an operating
waterfall, a construction funding plan, a promote, a sweep, feed shared out
in tonnes. Step 3 tested it against the old engine's promote (16 convention
variants, zero difference) and a funding plan
(``consolidation/experiments/alloc.py``).

The operator
------------
Tiers are visited in order. Each is paid ``min(remaining, cap)``, where the
cap is a number or a function of what earlier tiers were paid (a catch-up
reads the pref paid before it). A tier's payment is split between its
recipients by shares that sum to one. Whatever is left is the **residual**,
and it has a name chosen by the rulebook ("distribution", "equity",
"feed gap"), so a gap is never a silent plug.

A tier may be *blocked* by a named test (a DSCR lock-up). It is then paid
nothing, and the allocation records which test blocked it; the cash it would
have taken stays in the residual, whose destination the caller decides.

What it does not do
-------------------
It never computes a coverage ratio or evaluates a test (Measures and
Conditions do), and it never posts: the caller turns the result into
postings through the contracts. It does not read the books.

Conservation (paid plus residual equals the amount) holds exactly, because
amounts are :class:`fractions.Fraction`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from one_engine.units import Number, exact


class AllocationError(ValueError):
    """An allocation is mis-specified."""


Cap = Union[Number, Callable[[Mapping[str, Fraction]], Number]]


@dataclass(frozen=True)
class Tier:
    """One tier: its name, its cap, how its payment is split, and any block.

    ``cap`` is the amount the tier may take: a number, or a function of the
    payments made so far (``{tier name: paid}``) for caps that depend on
    earlier tiers. ``shares`` maps recipients to shares summing to one;
    the default sends everything to the tier itself.
    """

    name: str
    cap: Cap
    shares: Mapping[str, Number] = field(default_factory=lambda: {"": 1})
    blocked_by: Optional[str] = None


@dataclass(frozen=True)
class Allocation:
    """The result: payments per tier and per (tier, recipient), and the residual."""

    amount: Fraction
    paid: Dict[str, Fraction]
    split: Dict[Tuple[str, str], Fraction]
    blocked: Dict[str, str]
    residual_name: str
    residual: Fraction


def allocate(amount: Number, tiers: Sequence[Tier], residual: str) -> Allocation:
    """Divide ``amount`` over ``tiers`` in order; name what is left ``residual``."""
    amount = exact(amount)
    if amount < 0:
        raise AllocationError(f"cannot allocate a negative amount ({amount})")
    if not residual:
        raise AllocationError("the residual must be named")
    names = [t.name for t in tiers]
    if len(set(names)) != len(names):
        raise AllocationError(f"a tier is named twice: {names}")
    paid: Dict[str, Fraction] = {}
    split: Dict[Tuple[str, str], Fraction] = {}
    blocked: Dict[str, str] = {}
    remaining = amount
    for tier in tiers:
        shares = {k: exact(v) for k, v in tier.shares.items()}
        if sum(shares.values()) != 1 or any(v < 0 for v in shares.values()):
            raise AllocationError(f"tier '{tier.name}': shares must be non-negative and sum to 1, got {shares}")
        cap = exact(tier.cap(dict(paid)) if callable(tier.cap) else tier.cap)
        if cap < 0:
            raise AllocationError(f"tier '{tier.name}': negative cap {cap}")
        if tier.blocked_by:
            blocked[tier.name] = tier.blocked_by
            x = Fraction(0)
        else:
            x = min(remaining, cap)
        paid[tier.name] = x
        for who, share in shares.items():
            split[(tier.name, who)] = x * share
        remaining -= x
    return Allocation(amount, paid, split, blocked, residual, remaining)
