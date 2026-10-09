"""Contracts: one runner for every agreement (module card 6).

What a contract type is
-----------------------
A :class:`ContractType` is wiring plus one small function:

* the closed set of **terms** it reads. A term it does not know, or a
  required term that is missing, refuses (golds F-480: a term that was
  stated, unlisted, accepted and silently ignored);
* its **parties** (roles such as ``provider`` and ``client``) and, per
  party, the **accounts** it may touch, with their natures. A contract can
  post nowhere else;
* its **flows**: each names a phase and one *leg* per party book it
  touches, with the debit, the credit and the declared effect on both.
  A flow between two entities has two legs that post the same amount in
  both books under one event id, which is how intercompany flows stay
  paired (card 5);
* an **amounts** function: given the term values and a period, how much
  each flow moves. That function is the only arithmetic in a contract type.

Contract types are packs (:mod:`one_engine.packs.contracts`); this module
is shared and holds no deal-specific arithmetic.

State travels
-------------
A flow's amount is DERIVED from the terms, unless a term it reads is a
DEFAULT or a STAND_IN; then the amount takes the weakest of those states,
so a guessed tariff is visible on every posting it produced.

Not built yet (added when a case needs it)
------------------------------------------
Due versus paid. For now every amount due is posted as paid. Averta's
shareholder loan (cash interest only as far as the waterfall allows) is the
case that will add it, with Allocation returning what was paid.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Tuple

from one_engine.inputs import State, Value
from one_engine.kernel import Nature
from one_engine.units import exact


class ContractError(ValueError):
    """A contract is mis-specified: unknown or missing terms or parties."""


# Weakest first: a flow takes the weakest state among the terms it reads.
_WEAKNESS = [State.NOT_PRINTED, State.STAND_IN, State.DEFAULT, State.DERIVED, State.STATED]


@dataclass(frozen=True)
class Leg:
    """One party's posting for a flow: debit, credit, declared effects (debit, credit)."""

    party: str
    debit: str
    credit: str
    effect: Tuple[int, int]


@dataclass(frozen=True)
class FlowSpec:
    """A named flow of a contract type: its phase and one leg per book."""

    name: str
    phase: str
    legs: Tuple[Leg, ...]


@dataclass(frozen=True)
class ContractType:
    """The wiring of one kind of agreement. See the module docstring."""

    name: str
    terms: Tuple[str, ...]
    parties: Tuple[str, ...]
    accounts: Mapping[str, Tuple[Tuple[str, Nature], ...]]
    flows: Tuple[FlowSpec, ...]
    amounts: Callable[[Mapping[str, Any], int], Mapping[str, Fraction]]

    def __post_init__(self):
        names = [f.name for f in self.flows]
        if len(set(names)) != len(names):
            raise ContractError(f"type '{self.name}': flow names repeat")
        for f in self.flows:
            for leg in f.legs:
                if leg.party not in self.parties:
                    raise ContractError(f"type '{self.name}', flow '{f.name}': unknown party '{leg.party}'")
                declared = {a for a, _ in self.accounts.get(leg.party, ())}
                for acct in (leg.debit, leg.credit):
                    if acct not in declared:
                        raise ContractError(
                            f"type '{self.name}', flow '{f.name}': account '{acct}' is not declared for '{leg.party}'")


@dataclass(frozen=True)
class Flow:
    """One flow of one contract in one period, ready to post."""

    event: str
    spec: FlowSpec
    period: int
    amount: Fraction
    state: State


class Contract:
    """An instance of a contract type: parties bound to entities, terms given.

    Parameters
    ----------
    contract_id:
        Unique name in the assembly; prefixes every event id.
    ctype:
        The :class:`ContractType`.
    parties:
        Role -> entity name, for exactly the type's roles.
    terms:
        Term name -> :class:`~one_engine.inputs.Value`, for exactly the
        type's terms.
    currency:
        The unit of every account the contract touches. It is a setting of
        the assembly, entered once, not a term repeated per contract.
    """

    def __init__(self, contract_id: str, ctype: ContractType, parties: Mapping[str, str],
                 terms: Mapping[str, Value], currency: str):
        self.id = contract_id
        self.type = ctype
        self.currency = currency
        if set(parties) != set(ctype.parties):
            raise ContractError(f"contract '{contract_id}' ({ctype.name}): parties must be exactly "
                                f"{sorted(ctype.parties)}, got {sorted(parties)}")
        unknown = sorted(set(terms) - set(ctype.terms))
        if unknown:
            raise ContractError(f"contract '{contract_id}' ({ctype.name}): unknown term(s) {', '.join(unknown)}")
        missing = sorted(set(ctype.terms) - set(terms))
        if missing:
            raise ContractError(f"contract '{contract_id}' ({ctype.name}): missing term(s) {', '.join(missing)}")
        for k, v in terms.items():
            if not isinstance(v, Value):
                raise ContractError(f"contract '{contract_id}': term '{k}' must be a Value with a state")
        self.parties = dict(parties)
        self.terms = dict(terms)
        states = [v.state for v in terms.values()]
        weakest = min(states + [State.DERIVED], key=_WEAKNESS.index)
        self._state = weakest if _WEAKNESS.index(weakest) < _WEAKNESS.index(State.DERIVED) else State.DERIVED

    def accounts(self) -> Dict[str, Tuple[Tuple[str, Nature], ...]]:
        """Entity -> the accounts this contract needs in that entity's book."""
        out: Dict[str, Tuple[Tuple[str, Nature], ...]] = {}
        for role, entity in self.parties.items():
            out[entity] = out.get(entity, ()) + tuple(self.type.accounts.get(role, ()))
        return out

    def flows(self, period: int) -> List[Flow]:
        """The flows of this contract in ``period``, in the type's declared order."""
        values = {k: v.value for k, v in self.terms.items()}
        amounts = self.type.amounts(values, period)
        out = []
        for spec in self.type.flows:
            if spec.name not in amounts:
                continue
            amount = exact(amounts[spec.name])
            if amount < 0:
                raise ContractError(f"contract '{self.id}', flow '{spec.name}', period {period}: "
                                    f"negative amount {amount}; direction belongs to the legs")
            if amount == 0:
                continue
            out.append(Flow(f"{self.id}:{spec.name}:{period}", spec, period, amount, self._state))
        return out
