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

``loops`` (optional, step 7e) declares fixed points closed across runs by
:mod:`one_engine.loops`: each names a rule from ``LOOP_RULES`` and its
outputs' starting values. Anywhere in the case, ``{loop: name, output:
x}`` (optionally ``times: f``) stands for that output's current value; in
a contract's terms it enters as a DERIVED value. A reference to an
undeclared loop or output, and a loop output nothing references, refuse.
The case is built afresh from the file for every run of the loop.

A measure ``xirr`` takes ``dates`` (one ISO date per period) and returns
an annual rate.
"""
from __future__ import annotations

import copy
import datetime as dt
import pathlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Union

from one_engine import measures as M
from one_engine.checks import integrity
from one_engine.contracts import Contract
from one_engine.cuts import Scheme, Series, cash_flow_cut
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader, State, Value, load_yaml_strict
from one_engine.loops import Loop, LoopReport, close
from one_engine.packs.contracts import REGISTRY
from one_engine.packs.pf import LOOP_RULES, RULEBOOKS
from one_engine.units import exact

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
    loops: Dict[str, LoopReport] = field(default_factory=dict)
    pending_measures: Dict[str, Callable[[], Any]] = field(default_factory=dict, repr=False)

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


def _loop_refs(node: Any, path: tuple = ()):
    """Every ``{loop: ...}`` reference in the case, with its path."""
    if isinstance(node, Mapping):
        if "loop" in node:
            yield path, node
            return
        for k, v in node.items():
            yield from _loop_refs(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _loop_refs(v, path + (i,))


def _substitute(node: Any, values: Mapping[str, Mapping[str, Any]], path: tuple = ()) -> Any:
    """The case with each loop reference replaced by the loop's current value (a fresh copy)."""
    if isinstance(node, Mapping):
        if "loop" in node:
            v = values[node["loop"]][node["output"]]
            if "times" in node:
                v = exact(v) * exact(node["times"])
            # a contract term carries its state; anywhere else the number is read as it is
            if len(path) == 4 and path[0] == "contracts" and path[2] == "terms":
                return {"value": v, "state": State.DERIVED.value, "source": f"loop {node['loop']}.{node['output']}"}
            return v
        return {k: _substitute(v, values, path + (k,)) for k, v in node.items()}
    if isinstance(node, list):
        return [_substitute(v, values, path + (i,)) for i, v in enumerate(node)]
    return copy.deepcopy(node)


def _read_loops(raw: Mapping[str, Any], body: Mapping[str, Any], src: str) -> List[Loop]:
    loops = []
    for name, spec in raw.items():
        r = Reader(spec, source=f"{src}:loops.{name}")
        rr = Reader(r.take("rule"), source=f"{src}:loops.{name}.rule")
        kind = rr.take("kind")
        if kind not in LOOP_RULES:
            raise InputError(f"{src}: loop '{name}' has unknown rule '{kind}'")
        rule = LOOP_RULES[kind](rr)
        rr.finish()
        loops.append(Loop(name, dict(r.take("start")), rule, float(r.take("tolerance")), int(r.take("max_iterations"))))
        r.finish()
    outputs = {(lp.name, k) for lp in loops for k in lp.start}
    read = set()
    for path, ref in _loop_refs(body):
        where = f"{src}:{'.'.join(str(p) for p in path)}"
        extra = sorted(set(ref) - {"loop", "output", "times"})
        if extra:
            raise InputError(f"{where}: a loop reference takes loop, output and times, not {', '.join(extra)}")
        if (ref["loop"], ref.get("output")) not in outputs:
            # an undeclared circular read: the run would use a value no loop closes
            raise InputError(f"{where}: reads loop '{ref['loop']}' output '{ref.get('output')}', which no loop declares")
        read.add((ref["loop"], ref["output"]))
    unread = sorted(outputs - read)
    if unread:
        raise InputError(f"{src}: loop outputs read by nothing: {', '.join(f'{a}.{b}' for a, b in unread)}")
    return loops


def run_case(case: Union[str, pathlib.Path, Mapping[str, Any]]) -> CaseResult:
    """Run a case file (path) or an already-parsed case (mapping), closing its loops if it declares any."""
    if isinstance(case, (str, pathlib.Path)):
        path = pathlib.Path(case)
        data, src = load_yaml_strict(path.read_text()), path.name
    else:
        data, src = case, "<case>"
    body = {k: v for k, v in data.items() if k != "loops"}
    if "loops" not in data:
        refs = [".".join(str(p) for p in path) for path, _ in _loop_refs(body)]
        if refs:
            raise InputError(f"{src}: {', '.join(refs)} read a loop, and the case declares none")
        return _measure(_run(body, src))
    loops = _read_loops(data["loops"], body, src)
    result, reports = close(loops, lambda values: _run(_substitute(body, values), src))
    result.loops = reports
    return _measure(result)


def _date(x: Any) -> dt.date:
    return x if isinstance(x, dt.date) else dt.date.fromisoformat(str(x))


def _run(data: Mapping[str, Any], src: str) -> CaseResult:
    """Build the assembly from the case data, run it, and read its cuts, measures and checks."""
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
    # measures are read here and evaluated once the run is accepted: a loop's
    # first runs (a loan of zero) may have no return to measure
    pending: Dict[str, Callable[[], Any]] = {}
    measure_cut: Dict[str, str] = {}
    for mname, raw in top.take("measures").items():
        r = Reader(raw, source=f"{src}:measures.{mname}")
        kind, cut = r.take("measure"), r.take("cut")
        if kind == "xirr":
            dates = [_date(d) for d in r.take("dates")]
            pending[mname] = lambda c=cut, d=dates: M.xirr(cuts[c].values, d)
        elif kind == "irr":
            per_year = int(r.take_or_default("per_year", 1, reason="the stream's periods are years").value)
            pending[mname] = lambda c=cut, n=per_year: (1 + M.irr(cuts[c].values)) ** n - 1
        else:
            pending[mname] = lambda c=cut, k=kind: MEASURES[k](cuts[c].values)
        measure_cut[mname] = cut
        r.finish()
    wind_down = top.take_or_default("wind_down", {}, reason="no terminal wind-down declared").value
    top.finish()
    verdicts = {e: integrity(b.table(), b.accounts, wind_down=tuple(wind_down.get(e, ())))
                for e, b in group.books.items()}
    rule_checks = {n: fn() for n, fn in rule_check_fns.items()}
    out = CaseResult(name, group, cuts, {}, measure_cut, verdicts, rule_checks)
    out.pending_measures = pending
    return out


def _measure(result: CaseResult) -> CaseResult:
    """Evaluate the accepted run's measures, in file order."""
    result.measures = {m: fn() for m, fn in result.pending_measures.items()}
    return result
