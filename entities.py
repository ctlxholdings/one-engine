"""Entities: the books of a group and the flows between them (module card 5).

A :class:`Group` holds one :class:`~one_engine.kernel.Book` per entity and
runs an assembly over the period grid, period by period and phase by phase.

In each phase of each period, in this order:

1. every contract flow of that phase is posted. A flow with legs in two
   books is posted in both under one event id, so an intercompany fee
   cannot be booked on one side only (:meth:`Group.unpaired_flows`
   re-checks that from the books);
2. the rulebook handlers registered for that phase run (a funding plan, a
   tax charge, an operating waterfall). They read balances, decide amounts
   through Allocation, and post only through the contracts' legs or
   through accounts a rulebook declared;
3. shortfall legs queued for that phase are posted (a shareholder loan's
   unpaid interest, capitalised at period close).

Due versus paid (card 6) is closed here: a handler asks for the claims due
(:meth:`Group.claims`), allocates, and calls :meth:`Group.settle` with the
amount paid. Settlements are recorded for reports.

Built so far: books, contract flows, pairing, handlers, claims. Not yet:
ownership, functional currencies, consolidation with eliminations (added
when a case needs a multi-entity chain).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from one_engine.contracts import Balances, Claim, Contract, Leg
from one_engine.inputs import State
from one_engine.kernel import Account, Book, KernelError, Nature, Posting
from one_engine.units import Number, exact

Handler = Callable[["Group", int], None]


@dataclass(frozen=True)
class Settlement:
    """What a claim was due and what was paid, in one period."""

    ref: str
    period: int
    due: Fraction
    paid: Fraction


class Group:
    """The entities of one assembly, its contracts and its rulebook handlers.

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
        self._declared: Dict[str, Dict[str, Tuple[Nature, str]]] = defaultdict(dict)
        self._handlers: Dict[str, List[Handler]] = defaultdict(list)
        self._queued: Dict[Tuple[int, str], List[Tuple[Contract, Leg, Fraction, str, State]]] = defaultdict(list)
        self._books: Dict[str, Book] = {}
        self._legs: Dict[str, int] = {}
        self.settlements: List[Settlement] = []
        self.records: List[Dict[str, Any]] = []

    # ── assembly ────────────────────────────────────────────────────────────

    def add_contract(self, c: Contract) -> None:
        if any(x.id == c.id for x in self.contracts):
            raise KernelError(f"contract id '{c.id}' is used twice")
        if self._books:
            raise KernelError("contracts must be added before the group runs")
        self.contracts.append(c)

    def contract(self, cid: str) -> Contract:
        for c in self.contracts:
            if c.id == cid:
                return c
        raise KernelError(f"no contract '{cid}'")

    def declare(self, entity: str, name: str, nature: Nature, unit: str) -> None:
        """Declare an account a rulebook posts to (tax payable, fixed assets)."""
        if self._books:
            raise KernelError("accounts must be declared before the group runs")
        self._merge(entity, name, nature, unit, who="a rulebook")

    def register(self, phase: str, handler: Handler) -> None:
        """Run ``handler(group, period)`` in ``phase`` of every period, after that phase's flows."""
        if phase not in self.phases:
            raise KernelError(f"handler registered for unknown phase '{phase}'")
        self._handlers[phase].append(handler)

    def _merge(self, entity, name, nature, unit, who):
        prev = self._declared[entity].get(name)
        if prev and prev != (nature, unit):
            raise KernelError(f"{entity}.{name} is declared as {prev} and as {(nature, unit)} by {who}")
        self._declared[entity][name] = (nature, unit)

    def _build_books(self) -> None:
        for c in self.contracts:
            for entity, accts in c.accounts().items():
                for name, nature in accts:
                    self._merge(entity, name, nature, c.currency, who=f"contract '{c.id}'")
        for (entity, name) in self.floors:
            if name not in self._declared.get(entity, {}):
                raise KernelError(f"floor set on {entity}.{name}, which nothing declares")
        for entity, accts in self._declared.items():
            self._books[entity] = Book(
                entity=entity, periods=self.periods, phases=self.phases,
                accounts=[Account(n, nat, unit, floor=self.floors.get((entity, n)))
                          for n, (nat, unit) in accts.items()])

    # ── running ─────────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run every period: per phase, flows, then handlers, then queued shortfalls."""
        self._build_books()
        for t in range(self.periods):
            for phase in self.phases:
                for c in self.contracts:
                    for f in c.flows(t, self.view(c, t)):
                        if f.spec.phase != phase:
                            continue
                        self._event(f.event, len(f.spec.legs))
                        for leg in f.spec.legs:
                            self._post(c, leg, f.amount, t, phase, f.event, f.state)
                for h in self._handlers[phase]:
                    h(self, t)
                for c, leg, amount, event, state in self._queued.pop((t, phase), []):
                    self._event(event, 1)
                    self._post(c, leg, amount, t, phase, event, state)
            for b in self._books.values():
                b.close_period(t)

    def _event(self, event: str, legs: int) -> None:
        # one event id, one economic event: a reused id would let the pairing
        # check count one event's postings against another's
        if event in self._legs:
            raise KernelError(f"event id '{event}' is used twice")
        self._legs[event] = legs

    def view(self, c: Contract, t: int) -> Balances:
        return Balances(c, self._books, t)

    def _post(self, c: Contract, leg: Leg, amount: Fraction, t: int, phase: str, event: str, state: State) -> None:
        debit, credit = c.acct(leg.debit), c.acct(leg.credit)
        self._books[c.parties[leg.party]].post(Posting(
            period=t, phase=phase, debit=debit, credit=credit, amount=amount,
            effect={debit: leg.effect[0], credit: leg.effect[1]},
            source=f"contracts:{c.type.name}", event=event, state=state))

    # ── services for handlers ───────────────────────────────────────────────

    def claims(self, t: int) -> Dict[str, Claim]:
        """Every claim due in period ``t``, keyed ``contract.claim``."""
        return {cl.ref: cl for c in self.contracts for cl in c.claims(t, self.view(c, t))}

    def settle(self, claim: Claim, paid: Number, phase: str) -> None:
        """Post the paid part of ``claim`` now; queue its shortfall leg, if any."""
        paid = exact(paid)
        if not 0 <= paid <= claim.due:
            raise KernelError(f"claim {claim.ref}: paid {paid} is outside 0..{claim.due}")
        c, t = claim.contract, claim.period
        if paid > 0:
            event = f"{c.id}:claim.{claim.spec.name}:{t}"     # a flow may share the claim's name
            self._event(event, 1)
            self._post(c, claim.spec.leg, paid, t, phase, event, claim.state)
        unpaid = claim.due - paid
        if unpaid > 0 and claim.spec.shortfall is not None:
            sp = claim.spec.shortfall_phase
            if self.phases.index(sp) < self.phases.index(phase):
                raise KernelError(f"claim {claim.ref}: shortfall phase '{sp}' is before '{phase}'")
            self._queued[(t, sp)].append((c, claim.spec.shortfall, unpaid,
                                          f"{c.id}:claim.{claim.spec.name}:{t}:shortfall", claim.state))
        self.settlements.append(Settlement(claim.ref, t, claim.due, paid))

    def post_leg(self, c: Contract, leg_name: str, amount: Number, t: int, phase: str) -> None:
        """Post a named leg of ``c`` with an amount a rulebook decided (a residual)."""
        amount = exact(amount)
        if amount == 0:
            return
        event = f"{c.id}:leg.{leg_name}:{t}"
        self._event(event, 1)
        self._post(c, c.type.legs[leg_name], amount, t, phase, event, State.DERIVED)

    def post(self, entity: str, posting: Posting) -> None:
        """Post to an account a rulebook declared."""
        self._event(posting.event, 1)
        self._books[entity].post(posting)

    def record(self, kind: str, period: int, **data: Any) -> None:
        """Keep a fact for reports (a test verdict, a trapped residual)."""
        self.records.append({"kind": kind, "period": period, **data})

    # ── reading ─────────────────────────────────────────────────────────────

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
