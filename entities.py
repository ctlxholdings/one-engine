"""Entities: the books of a group and the flows between them (module card 5).

A :class:`Group` holds one :class:`~one_engine.kernel.Book` per entity and
runs the contracts of an assembly over the period grid, period by period
and phase by phase. A contract flow with legs in two books is posted in
both under one event id, so an intercompany fee cannot be booked on one
side only. :meth:`Group.unpaired_flows` re-checks that from the books.

Built so far: books, contract flows, pairing. Not yet: ownership,
functional currencies, consolidation with eliminations (added when Averta's
SPV-HoldCo chain needs them).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from one_engine.contracts import Contract
from one_engine.kernel import Account, Book, KernelError, Nature, Posting
from one_engine.units import Number


class Group:
    """The entities of one assembly and the contracts between them.

    Parameters
    ----------
    periods, phases:
        The clock shared by every book.
    floors:
        ``{(entity, account): floor}``. Floors are an assembly decision (a
        cash floor of zero unless a facility funds an overdraft), not a
        property of a contract type.
    """

    def __init__(self, periods: int, phases: Sequence[str],
                 floors: Optional[Mapping[Tuple[str, str], Number]] = None):
        self.periods = periods
        self.phases = tuple(phases)
        self.floors = dict(floors or {})
        self.contracts: List[Contract] = []
        self._books: Dict[str, Book] = {}
        self._legs: Dict[str, int] = {}

    def add_contract(self, c: Contract) -> None:
        if any(x.id == c.id for x in self.contracts):
            raise KernelError(f"contract id '{c.id}' is used twice")
        if self._books:
            raise KernelError("contracts must be added before the group runs")
        self.contracts.append(c)

    def _build_books(self) -> None:
        decl: Dict[str, Dict[str, Tuple[Nature, str]]] = defaultdict(dict)
        for c in self.contracts:
            for entity, accts in c.accounts().items():
                for name, nature in accts:
                    prev = decl[entity].get(name)
                    if prev and prev != (nature, c.currency):
                        raise KernelError(f"{entity}.{name} is declared as {prev} and as {(nature, c.currency)}")
                    decl[entity][name] = (nature, c.currency)
        for (entity, name) in self.floors:
            if name not in decl.get(entity, {}):
                raise KernelError(f"floor set on {entity}.{name}, which no contract declares")
        for entity, accts in decl.items():
            self._books[entity] = Book(
                entity=entity, periods=self.periods, phases=self.phases,
                accounts=[Account(n, nat, unit, floor=self.floors.get((entity, n)))
                          for n, (nat, unit) in accts.items()])

    def run(self) -> None:
        """Post every contract's flows, period by period, phase by phase."""
        self._build_books()
        for t in range(self.periods):
            flows = [f for c in self.contracts for f in c.flows(t)]
            for phase in self.phases:
                for f in (f for f in flows if f.spec.phase == phase):
                    c = next(c for c in self.contracts if f.event.startswith(c.id + ":"))
                    self._legs[f.event] = len(f.spec.legs)
                    for leg in f.spec.legs:
                        entity = c.parties[leg.party]
                        self._books[entity].post(Posting(
                            period=t, phase=phase, debit=leg.debit, credit=leg.credit,
                            amount=f.amount, effect={leg.debit: leg.effect[0], leg.credit: leg.effect[1]},
                            source=f"contracts:{c.type.name}", event=f.event, state=f.state))
            for b in self._books.values():
                b.close_period(t)

    def book(self, entity: str) -> Book:
        return self._books[entity]

    @property
    def books(self) -> Dict[str, Book]:
        return dict(self._books)

    def unpaired_flows(self) -> List[str]:
        """Flow events whose legs are not all present, with one amount, in the books."""
        seen: Dict[str, List] = defaultdict(list)
        for b in self._books.values():
            for p in b.postings:
                seen[p.event].append(p.amount)
        return sorted(e for e, n in self._legs.items()
                      if len(seen.get(e, [])) != n or len(set(seen[e])) != 1)
