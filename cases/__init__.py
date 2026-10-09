"""Case runner: turn a case file into an assembly, run it, read its results.

A case file names everything an assembly needs: the clock, the currency,
floors, contracts (by pack type), classification schemes, cuts and
measures. Every key is accounted for by a :class:`~one_engine.inputs.Reader`;
a key nobody reads stops the run (the nightmare fixture's ignored
``promote`` block is the planted case in ``tests/test_case_eria.py``).

A term is either a plain number (STATED, sourced to the case file and key)
or ``{value, state, source}`` to carry another state, such as a stand-in.
"""
from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Union

from one_engine import measures as M
from one_engine.checks import integrity
from one_engine.contracts import Contract
from one_engine.cuts import Scheme, Series, cash_flow_cut
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader, State, Value, load_yaml_strict
from one_engine.packs.contracts import REGISTRY

MEASURES = {"irr": M.irr, "payback": M.payback}


@dataclass
class CaseResult:
    """A run's group, its named cuts and measures, and its check verdicts."""

    name: str
    group: Group
    cuts: Dict[str, Series]
    measures: Dict[str, Any]
    measure_cut: Dict[str, str]
    verdicts: Dict[str, list] = field(default_factory=dict)

    def cut(self, name: str) -> Series:
        return self.cuts[name]

    def measure(self, name: str) -> Any:
        return self.measures[name]

    def measure_states(self, name: str):
        return set(self.cuts[self.measure_cut[name]].states)

    def failed_checks(self) -> List[str]:
        return [f"{entity}:{v.name} {v.detail}" for entity, vs in self.verdicts.items() for v in vs if not v.passed]


def _term(raw: Any, where: str) -> Value:
    if isinstance(raw, Mapping):
        r = Reader(raw, source=where)
        state = State(r.take("state"))
        v = Value(r.take("value"), state, r.take("source"))
        r.finish()
        return v
    return Value(raw, State.STATED, where)


def run_case(case: Union[str, pathlib.Path, Mapping[str, Any]]) -> CaseResult:
    """Run a case file (path) or an already-parsed case (mapping)."""
    if isinstance(case, (str, pathlib.Path)):
        path = pathlib.Path(case)
        data, src = load_yaml_strict(path.read_text()), path.name
    else:
        data, src = case, "<case>"
    top = Reader(data, source=src)
    name = top.take("case")
    currency = top.take("currency")
    floors = {}
    fr = Reader(top.take("floors"), source=f"{src}:floors")
    for key in list(top.take("floors")):
        entity, account = key.split(".", 1)
        floors[(entity, account)] = fr.take(key)
    fr.finish()

    group = Group(periods=int(top.take("periods")), phases=top.take("phases"), floors=floors)
    for cid, raw in top.take("contracts").items():
        r = Reader(raw, source=f"{src}:contracts.{cid}")
        tname = r.take("type")
        if tname not in REGISTRY:
            raise InputError(f"{src}: contract '{cid}' has unknown type '{tname}'")
        terms = {k: _term(v, f"{src}:contracts.{cid}.terms.{k}") for k, v in r.take("terms").items()}
        group.add_contract(Contract(cid, REGISTRY[tname], r.take("parties"), terms, currency))
        r.finish()
    group.run()

    schemes = {n: Scheme(n, dict(m)) for n, m in top.take("schemes").items()}
    cuts: Dict[str, Series] = {}
    for cname, raw in top.take("cuts").items():
        r = Reader(raw, source=f"{src}:cuts.{cname}")
        cuts[cname] = cash_flow_cut(group.book(r.take("entity")), r.take("account"),
                                    schemes[r.take("scheme")], r.take("include"), name=cname)
        r.finish()
    measures: Dict[str, Any] = {}
    measure_cut: Dict[str, str] = {}
    for mname, raw in top.take("measures").items():
        r = Reader(raw, source=f"{src}:measures.{mname}")
        fn, cut = MEASURES[r.take("measure")], r.take("cut")
        measures[mname] = fn(cuts[cut].values)
        measure_cut[mname] = cut
        r.finish()
    top.finish()
    verdicts = {e: integrity(b.table(), b.accounts) for e, b in group.books.items()}
    return CaseResult(name, group, cuts, measures, measure_cut, verdicts)
