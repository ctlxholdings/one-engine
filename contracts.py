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
* an **amounts** function: given the term values, a period and a read-only
  view of the contract's own balances, how much each flow moves. Contract
  state lives in the kernel (a loan's balance is its debt account), so the
  function reads it rather than keeping a private copy;
* optionally, **claims**: amounts *due* that Allocation decides whether to
  pay (or, for a source such as a loan draw, how much to take). Each claim
  names the leg posted for the part paid and, if the contract says so, the
  leg posted for the part not paid (a PIK capitalisation). A claim with no
  shortfall leg simply stays due: the payable balance carries it as arrears.

Contract types are packs (:mod:`one_engine.packs.contracts`); this module
is shared and holds no deal-specific arithmetic.

State travels
-------------
A flow's amount is DERIVED from the terms, unless a term it reads is a
DEFAULT or a STAND_IN; then the amount takes the weakest of those states,
so a guessed tariff is visible on every posting it produced.

Due versus paid
---------------
Flows are posted as computed. Claims are not: the contract states what is
due, Allocation returns what is paid, and the contract's own terms (its
shortfall leg, or none) decide what the unpaid part becomes. Averta's
shareholder loan, paid in cash only as far as the waterfall allows and
capitalised otherwise, is the case that added this.

Named legs, account names, references
-------------------------------------
* ``legs`` are named postings a rulebook may make under the contract with
  an amount it decides: an equity contribution as the residual of a
  funding plan, a distribution as the residual of a waterfall.
* An account name may contain ``{id}``, replaced by the contract id, so
  two loans in one book keep separate ``senior.debt`` and ``shl.debt``.
* ``refs`` name terms whose value is another contract's id, or a list of
  ids (a reserve sized on two loans' debt service); the assembly resolves
  them to the contracts themselves before the run.

Accounts in a second unit
-------------------------
An account is ``(name, nature)``, in the contract's currency (the book's),
or ``(name, nature, term)``, in the unit named by that term's value. A
dollar loan in a euro book keeps its euro carrying amount in
``{id}.debt`` and its dollar principal in a dollar account beside it; the
kernel's per-unit identity then holds for each currency separately
(nightmare case, step 7).

Quantities a rulebook hands to a claim
--------------------------------------
Some claims depend on a figure only a rulebook computes: a loan sculpted to
a target DSCR owes CFADS / target less interest. The rulebook passes such
figures by name (``g.claims(t, given={"cfads": x})``) and the contract reads
them with ``bal.given("cfads")``; asking for a figure that was not passed
refuses, so a claim is never computed on a silent zero. ``bal.of(other)``
reads another contract's balances in the same period (a reserve sizing
itself on the loans it covers).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

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
class ClaimSpec:
    """A claim of a contract type: what is posted when it is paid, and when it is not.

    ``kind`` is ``"pay"`` for an amount the party owes (paid out of cash) or
    ``"source"`` for an amount the party may take (a draw into cash).
    ``shortfall`` is the leg posted for the unpaid part in ``shortfall_phase``;
    ``None`` leaves the unpaid part where it is (arrears, or an undrawn facility).
    """

    name: str
    kind: str
    leg: Leg
    shortfall: Optional[Leg] = None
    shortfall_phase: str = "close"


@dataclass(frozen=True)
class ContractType:
    """The wiring of one kind of agreement. See the module docstring."""

    name: str
    terms: Tuple[str, ...]
    parties: Tuple[str, ...]
    accounts: Mapping[str, Tuple[Tuple[str, Nature], ...]]
    flows: Tuple[FlowSpec, ...]
    amounts: Callable[[Mapping[str, Any], int, "Balances"], Mapping[str, Fraction]]
    claim_specs: Tuple[ClaimSpec, ...] = ()
    claims: Optional[Callable[[Mapping[str, Any], int, "Balances"], Mapping[str, Fraction]]] = None
    legs: Mapping[str, Leg] = field(default_factory=dict)
    refs: Tuple[str, ...] = ()

    def __post_init__(self):
        names = [f.name for f in self.flows]
        if len(set(names)) != len(names):
            raise ContractError(f"type '{self.name}': flow names repeat")
        if bool(self.claim_specs) != (self.claims is not None):
            raise ContractError(f"type '{self.name}': claim_specs and claims go together")
        for c in self.claim_specs:
            if c.kind not in ("pay", "source"):
                raise ContractError(f"type '{self.name}', claim '{c.name}': kind must be 'pay' or 'source'")
        legs = [(f.name, leg) for f in self.flows for leg in f.legs]
        legs += [(c.name, leg) for c in self.claim_specs for leg in (c.leg, c.shortfall) if leg]
        legs += list(self.legs.items())
        if not set(self.refs) <= set(self.terms):
            raise ContractError(f"type '{self.name}': refs must be terms, got {self.refs}")
        for role, accts in self.accounts.items():
            for a in accts:
                if len(a) == 3 and a[2] not in self.terms:
                    raise ContractError(f"type '{self.name}': account '{a[0]}' takes its unit from "
                                        f"unknown term '{a[2]}'")
        for fname, leg in legs:
            if leg.party not in self.parties:
                raise ContractError(f"type '{self.name}', '{fname}': unknown party '{leg.party}'")
            declared = {a[0] for a in self.accounts.get(leg.party, ())}
            for acct in (leg.debit, leg.credit):
                if acct not in declared:
                    raise ContractError(
                        f"type '{self.name}', '{fname}': account '{acct}' is not declared for '{leg.party}'")


class Balances:
    """Read-only balances of one contract's accounts, for one period.

    ``view(role, account)`` is the balance so far in the period;
    ``view.opening(role, account)`` the balance at its start (what interest
    accrues on). ``{id}`` in an account name is expanded.
    ``view.given(name)`` is a figure a rulebook passed for this call (see
    the module docstring); ``view.of(other)`` is the same view of another
    contract.
    """

    def __init__(self, contract: "Contract", books: Mapping[str, Any], period: int,
                 given: Optional[Mapping[str, Any]] = None):
        self._c, self._books, self.period = contract, books, period
        self._given = dict(given or {})

    def __call__(self, role: str, account: str) -> Fraction:
        return self._books[self._c.parties[role]].balance(self._c.acct(account), self.period)

    def opening(self, role: str, account: str) -> Fraction:
        return self._books[self._c.parties[role]].opening(self._c.acct(account), self.period)

    def given(self, name: str) -> Fraction:
        if name not in self._given:
            raise ContractError(f"contract '{self._c.id}', period {self.period}: needs '{name}', "
                                f"which the calling rulebook did not pass")
        return exact(self._given[name])

    def of(self, other: "Contract") -> "Balances":
        return Balances(other, self._books, self.period, self._given)


@dataclass(frozen=True)
class Claim:
    """An amount due under one claim of one contract in one period."""

    contract: "Contract"
    spec: ClaimSpec
    period: int
    due: Fraction
    state: State

    @property
    def ref(self) -> str:
        return f"{self.contract.id}.{self.spec.name}"


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

    def acct(self, name: str) -> str:
        """An account name of this contract's type, with ``{id}`` expanded."""
        return name.replace("{id}", self.id)

    def accounts(self) -> Dict[str, Tuple[Tuple[str, Nature, str], ...]]:
        """Entity -> the accounts (name, nature, unit) this contract needs in that entity's book."""
        out: Dict[str, Tuple[Tuple[str, Nature, str], ...]] = {}
        for role, entity in self.parties.items():
            out[entity] = out.get(entity, ()) + tuple(
                (self.acct(a[0]), a[1], self.terms[a[2]].value if len(a) == 3 else self.currency)
                for a in self.type.accounts.get(role, ()))
        return out

    def resolve(self, contracts: Mapping[str, "Contract"]) -> None:
        """Replace each reference term's contract id (or list of ids) by the contract(s) it names."""
        for k in self.type.refs:
            v = self.terms[k]
            ids = list(v.value) if isinstance(v.value, (list, tuple)) else [v.value]
            for cid in ids:
                if cid not in contracts:
                    raise ContractError(f"contract '{self.id}': term '{k}' names unknown contract '{cid}'")
            found = [contracts[cid] for cid in ids]
            self.terms[k] = Value(found if isinstance(v.value, (list, tuple)) else found[0], v.state, v.source)

    def values(self) -> Dict[str, Any]:
        return {k: v.value for k, v in self.terms.items()}

    def flows(self, period: int, bal: Optional[Balances] = None) -> List[Flow]:
        """The flows of this contract in ``period``, in the type's declared order."""
        amounts = self.type.amounts(self.values(), period, bal)
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

    def claims(self, period: int, bal: Balances) -> List[Claim]:
        """The claims due under this contract in ``period`` (zero claims omitted)."""
        if self.type.claims is None:
            return []
        due = self.type.claims(self.values(), period, bal)
        out = []
        for spec in self.type.claim_specs:
            amount = exact(due.get(spec.name, 0))
            if amount < 0:
                raise ContractError(f"contract '{self.id}', claim '{spec.name}', period {period}: negative due {amount}")
            if amount > 0:
                out.append(Claim(self, spec, period, amount, self._state))
        return out
