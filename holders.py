"""Holders: who holds which claim, and what each receives (module card 11).

First built for the Macabacus LBO (plan step 7f), whose returns are those
of five holders of one company: a sponsor and management holding its
common equity, a subordinated note and a preferred stock that each carry
an equity kicker, and a management performance pool.

What is here
------------
* :func:`dilute`: fully diluted ownership. Holders who paid for common
  equity own it pro rata to what they paid; each dilution layer then
  grants shares of the whole (a kicker, an option pool) and scales every
  earlier holding down by what it grants. The order of layers matters:
  a pool granted after the kickers dilutes them too. Exact arithmetic,
  so the shares sum to exactly one.
* :func:`holder_stream`: one holder's cash flows to a dated exit. Up to
  the exit, what the company paid the holder (the negative of the
  company's cash flows with the holder's instruments); at the exit, the
  holder's claim then outstanding plus its share of the equity value.
  After the exit, nothing. The company's books are not touched: the exit
  is a valuation, not a transaction (the workbook keeps running the
  company for ten years).

Cash truth (the card's invariant) holds by construction: every flow in a
stream before the exit is a posting the kernel recorded on the company's
cash, read through a cut, so no payment can be missed or added.

Not here: returns (Measures), the equity value itself (a lens over cuts,
in a pack), conversions and transfers between holders (added when a case
needs them).
"""
from __future__ import annotations

from fractions import Fraction
from typing import Dict, List, Mapping, Sequence

from one_engine.units import Number, exact


class HolderError(ValueError):
    """An ownership or a holder stream cannot be computed as stated."""


def dilute(paid: Mapping[str, Number], layers: Sequence[Mapping[str, Number]]) -> Dict[str, Fraction]:
    """Fully diluted ownership after each layer of grants.

    Parameters
    ----------
    paid:
        Holder -> amount paid for common equity. Undiluted ownership is
        each amount over the total.
    layers:
        In order, each a mapping holder -> share of the whole granted at
        that layer. Every holding before the layer is multiplied by
        ``1 - sum(layer)``; the grants are then added (to a holder already
        present or a new one).

    Returns
    -------
    Holder -> share, summing to exactly one.

    Raises
    ------
    HolderError
        If an amount paid is negative or they total zero, or a layer grants
        a negative share or a whole of one or more.
    """
    paid = {h: exact(v) for h, v in paid.items()}
    if any(v < 0 for v in paid.values()):
        raise HolderError(f"amounts paid must not be negative: {paid}")
    total = sum(paid.values(), Fraction(0))
    if total <= 0:
        raise HolderError("nothing was paid for the common equity, so there is nothing to own pro rata")
    own = {h: v / total for h, v in paid.items()}
    for i, layer in enumerate(layers):
        grants = {h: exact(s) for h, s in layer.items()}
        if any(s < 0 for s in grants.values()):
            raise HolderError(f"dilution layer {i} grants a negative share: {grants}")
        granted = sum(grants.values(), Fraction(0))
        if granted >= 1:
            raise HolderError(f"dilution layer {i} grants {granted}, the whole company or more")
        own = {h: s * (1 - granted) for h, s in own.items()}
        for h, s in grants.items():
            own[h] = own.get(h, Fraction(0)) + s
    assert sum(own.values()) == 1        # exact: a share lost or created would show here
    return own


def holder_stream(company_flows: Sequence[Number], exit_period: int, claim: Number, share: Number,
                  equity_value: Number) -> List[Fraction]:
    """One holder's cash flows, by period, for a holding that ends at ``exit_period``.

    Parameters
    ----------
    company_flows:
        The company's cash flows with this holder's instruments, by period,
        from the company's side (a draw or a contribution is positive,
        interest or a dividend paid is negative).
    exit_period:
        The period of the exit (an index into ``company_flows``).
    claim:
        What the holder is owed at the exit and is repaid then (a note's
        principal, a preferred stock's face value); zero for common equity.
    share:
        The holder's fully diluted share of the equity value.
    equity_value:
        The equity value at the exit.
    """
    if not 0 <= exit_period < len(company_flows):
        raise HolderError(f"exit period {exit_period} is outside the {len(company_flows)} periods of the stream")
    out = [-exact(x) if t <= exit_period else Fraction(0) for t, x in enumerate(company_flows)]
    out[exit_period] += exact(claim) + exact(share) * exact(equity_value)
    return out
