"""Case runner: turn a case file into an assembly, run it, read its results.

A case file names everything an assembly needs: the clock, the currency,
floors, contracts (by pack type), classification schemes, cuts and
measures. Every key is accounted for by a :class:`~one_engine.inputs.Reader`;
a key nobody reads stops the run (the nightmare fixture's ignored
``promote`` block is the planted case in ``tests/test_case_eria.py``).

A term is either a plain number (STATED, sourced to the case file and key)
or ``{value, state, source}`` to carry another state, such as a stand-in.

``rulebooks`` (optional) installs pack rulebooks (:mod:`one_engine.packs.pf`)
in file order; within a phase, handlers run in that order. A rulebook may
return named checks, which join the integrity verdicts. Rulebooks share
asset registers and distributors by rulebook name.
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
from one_engine.packs.pf import RULEBOOKS

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
    rule_checks: Dict[str, List] = field(default_factory=dict)

    def cut(self, name: str) -> Series:
        return self.cuts[name]

    def measure(self, name: str) -> Any:
        return self.measures[name]

    def measure_states(self, name: str):
        return set(self.cuts[self.measure_cut[name]].states)

    def failed_checks(self) -> List[str]:
        out = [f"{entity}:{v.name} {v.detail}" for entity, vs in self.verdicts.items() for v in vs if not v.passed]
        out += [f"{name}: {fails}" for name, fails in self.rule_checks.items() if fails]
        return out

    def records(self, kind: str) -> Dict[int, dict]:
        """The rulebooks' records of one kind, by period (one per period)."""
        return {r["period"]: r for r in self.group.records if r["kind"] == kind}


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
        terms = {k: _term(v, f"{src}:contracts.{cid}.terms.{k}") for k, v in (r.take("terms") or {}).items()}
        group.add_contract(Contract(cid, REGISTRY[tname], r.take("parties"), terms, currency))
        r.finish()
    by_id = {c.id: c for c in group.contracts}
    for c in group.contracts:
        c.resolve(by_id)

    schemes = {n: Scheme(n, dict(m)) for n, m in top.take("schemes").items()}
    rule_check_fns = {}
    rulebooks = top.take_or_default("rulebooks", {}, reason="a case with contracts only").value
    # what rulebooks share: asset registers (read by income tax) and distributors (a promote that
    # takes a waterfall's residual), each under the rulebook's name
    shared = {"currency": currency, "schemes": schemes, "registers": {}, "distributors": {}}
    for rname, raw in rulebooks.items():
        r = Reader(raw, source=f"{src}:rulebooks.{rname}")
        kind = r.take("kind")
        if kind not in RULEBOOKS:
            raise InputError(f"{src}: rulebook '{rname}' has unknown kind '{kind}'")
        for cname, fn in (RULEBOOKS[kind](group, r, {**shared, "name": rname}) or {}).items():
            rule_check_fns[f"{rname}.{cname}"] = fn
        r.finish()
    group.run()

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
        kind, cut = r.take("measure"), r.take("cut")
        value = MEASURES[kind](cuts[cut].values)
        if kind == "irr":
            per_year = int(r.take_or_default("per_year", 1, reason="the stream's periods are years").value)
            value = (1 + value) ** per_year - 1
        measures[mname] = value
        measure_cut[mname] = cut
        r.finish()
    wind_down = top.take_or_default("wind_down", {}, reason="no terminal wind-down declared").value
    top.finish()
    verdicts = {e: integrity(b.table(), b.accounts, wind_down=tuple(wind_down.get(e, ())))
                for e, b in group.books.items()}
    rule_checks = {n: fn() for n, fn in rule_check_fns.items()}
    return CaseResult(name, group, cuts, measures, measure_cut, verdicts, rule_checks)
