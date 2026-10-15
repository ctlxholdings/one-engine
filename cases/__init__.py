"""Case runner: turn a case file into an assembly, run it, read its results.

A case file names everything an assembly needs: the clock, the currency,
floors, contracts (by pack type), classification schemes, cuts and
measures. Every key is accounted for by a :class:`~one_engine.inputs.Reader`;
a key nobody reads stops the run (the nightmare fixture's ignored
``promote`` block is the planted case in ``tests/test_case_eria.py``).

A term is either a plain number (STATED, sourced to the case file and key)
or ``{value, state, source}`` to carry another state, such as a stand-in.

``rulebooks`` (optional) installs rulebooks, shared (:mod:`one_engine.rulebooks`)
or from a pack (:mod:`one_engine.packs.pf`), in file order; within a phase, handlers run in that order. A rulebook may
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

Added for the Macabacus LBO (step 7f):

* ``opening`` (optional): per entity, the balance sheet the books open on,
  each account with its ``nature`` and ``value``. One account per entity
  may have ``value: balancing``: it takes the amount that makes the
  opening balance (a balance sheet copied from a float workbook is out by
  about 1e-14, which the exact kernel would refuse). The amount is kept
  in :attr:`CaseResult.balancing`, so a test can bound it.
* ``lenses`` (optional): views a pack computes from the finished books,
  such as holders' returns at an exit (``LENSES``). Each returns named
  series, which measures read as ``<lens>.<series>``, and values kept in
  :attr:`CaseResult.lenses`. A lens never posts.
* the ``moic`` measure.
* The corporate pack's contract types and rulebooks are available beside
  the project-finance ones; a name defined twice refuses at import.

Added for the Macabacus long-form LBO (step 7g): the ``cash_on_cash``
measure.

Added for Averta's live model (step 8): ``generators`` (optional), the
asset generators of the packs (``GENERATORS``), run once before anything
else. Each takes its inputs from the case file and returns named outputs
(series or numbers). Anywhere in the case, ``{generator: name, output: x}``
stands for that output; in a contract's terms it enters as a DERIVED value.
Generators run in file order, and one may read an earlier one's outputs. A
reference to an undeclared generator or output refuses. The outputs are
kept in :attr:`CaseResult.generated`.

Also added for Averta's live model (step 8, the portfolio):

* the generator kind ``cohorts`` (:mod:`one_engine.portfolio`): it runs a
  template case (a file in this directory, with optional overrides by
  dotted path, ``set``) and returns the template's cuts summed over a
  deployment schedule; and ``instalments``, what the copies cost when
  each is paid in shares around its own start;
* a cut may be ``kind: pnl``: income less expense of the classes it
  includes, by period (:func:`one_engine.cuts.pnl_cut`), read from
  ``entity`` under ``scheme``; without ``kind`` a cut is a cash cut, as
  before.

Added for ModelOff 2014 "Time is Money" (step 7p): the ``npv`` measure
(``rate`` per period, the first flow undiscounted) and the ``xnpv`` measure
(an annual ``rate`` and ``dates``, one per period). Either may take
``through``, the last period it values (the paper values the first ten
years of a 23-year forecast); without it the whole stream is valued.

Added for the golds library (:mod:`one_engine.oracles.golds`): the
techno-economic pack (:mod:`one_engine.packs.tea`), the ``formulas``
generator (now :mod:`one_engine.formulas`) and the ``held_balance``,
``settle`` and ``asset_pool`` rulebooks (the first two now in
:mod:`one_engine.rulebooks.postings`).

Added for the failure panel (``cases/panel``): the obligations pack
(:mod:`one_engine.packs.obligations`), its ``duty``, ``prohibition``,
``claim``, ``put``, ``stack`` and ``pool`` rulebooks. Registration only;
the runner's behaviour is unchanged.
"""
from __future__ import annotations

import copy
import datetime as dt
import pathlib
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping, Union

from one_engine import measures as M
from one_engine.checks import integrity
from one_engine.contracts import Contract
from one_engine import portfolio as _portfolio
from one_engine.cuts import Scheme, Series, cash_flow_cut, pnl_cut
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader, State, Value, load_yaml_strict
from one_engine.loops import Loop, LoopReport, close
from one_engine.kernel import Nature
from one_engine.packs import contracts as _contracts, corporate as _corporate, obligations as _obligations, pf as _pf, solar as _solar, tea as _tea
from one_engine.rulebooks.loops import LOOP_RULES
from one_engine import formulas as _formulas
from one_engine import rulebooks as _shared
from one_engine.units import exact

MEASURES = {"irr": M.irr, "payback": M.payback, "moic": M.moic, "cash_on_cash": M.cash_on_cash}


def _merge(*registries: Mapping[str, Any]) -> Dict[str, Any]:
    """One registry from several packs; a name two packs define is a conflict, not an override."""
    out: Dict[str, Any] = {}
    for reg in registries:
        twice = sorted(set(out) & set(reg))
        if twice:
            raise ImportError(f"pack names defined twice: {twice}")
        out.update(reg)
    return out


REGISTRY = _merge(_contracts.REGISTRY, _corporate.TYPES)
RULEBOOKS = _merge(_shared.RULEBOOKS, _pf.RULEBOOKS, _corporate.RULEBOOKS, _tea.RULEBOOKS, _obligations.RULEBOOKS)
LENSES = _merge(_corporate.LENSES)
CASES = pathlib.Path(__file__).resolve().parent


def _run_template(name: str, overrides: Mapping[str, Any]) -> "CaseResult":
    """Run the case file ``name`` in this directory, with each ``a.b.c: value`` override set first.

    An override may add a key, but only under a mapping the template has:
    a misspelt path would otherwise run the template unchanged.
    """
    path = (CASES / str(name)).resolve()
    if path.parent != CASES or not path.is_file():
        raise InputError(f"template '{name}' is not a case file in {CASES}")
    data = load_yaml_strict(path.read_text())
    for dotted, value in overrides.items():
        *parents, leaf = str(dotted).split(".")
        node = data
        for k in parents:
            if not isinstance(node, Mapping) or k not in node:
                raise InputError(f"template {name}: override '{dotted}' names '{k}', which the template does not have")
            node = node[k]
        if not isinstance(node, Mapping):
            raise InputError(f"template {name}: override '{dotted}' does not end in a mapping")
        node[leaf] = value
    return run_case(data)


GENERATORS = _merge(_formulas.GENERATORS, _solar.GENERATORS, _pf.GENERATORS,
                    {"cohorts": lambda r: _portfolio.cohorts(r, _run_template),
                     "instalments": _portfolio.instalments_generator})


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
    lenses: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    balancing: Dict[str, tuple] = field(default_factory=dict)
    generated: Dict[str, Dict[str, Any]] = field(default_factory=dict)
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


def _generator_refs(node: Any, path: tuple = ()):
    """Every ``{generator: ...}`` reference in the case, with its path."""
    if isinstance(node, Mapping):
        if "generator" in node and "kind" not in node:
            yield path, node
            return
        for k, v in node.items():
            yield from _generator_refs(v, path + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _generator_refs(v, path + (i,))


def _generate(raw: Mapping[str, Any], body: Mapping[str, Any], src: str):
    """Run the generators in file order; return their outputs and the case with every reference replaced."""
    outputs: Dict[str, Dict[str, Any]] = {}

    def replace(node, path=()):
        if isinstance(node, Mapping):
            if "generator" in node and "kind" not in node:
                where = f"{src}:{'.'.join(str(p) for p in path)}"
                extra = sorted(set(node) - {"generator", "output"})
                if extra:
                    raise InputError(f"{where}: a generator reference takes generator and output, not {', '.join(extra)}")
                g, o = node["generator"], node.get("output")
                if g not in outputs or o not in outputs[g]:
                    raise InputError(f"{where}: reads generator '{g}' output '{o}', which no earlier generator makes")
                v = outputs[g][o]
                # a contract term carries its state; anywhere else the value is read as it is
                if len(path) == 4 and path[0] == "contracts" and path[2] == "terms":
                    return {"value": v, "state": State.DERIVED.value, "source": f"generator {g}.{o}"}
                return v
            return {k: replace(v, path + (k,)) for k, v in node.items()}
        if isinstance(node, list):
            return [replace(v, path + (i,)) for i, v in enumerate(node)]
        return node

    for name, spec in raw.items():
        spec = replace(spec, ("generators", name))
        r = Reader(spec, source=f"{src}:generators.{name}")
        kind = r.take("kind")
        if kind not in GENERATORS:
            raise InputError(f"{src}: generator '{name}' has unknown kind '{kind}'")
        outputs[name] = GENERATORS[kind](r)
        r.finish()
    return outputs, replace(body)


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
    body = {k: v for k, v in data.items() if k not in ("loops", "generators")}
    generated: Dict[str, Dict[str, Any]] = {}
    if "generators" in data:
        generated, body = _generate(data["generators"], body, src)
    else:
        refs = [".".join(str(p) for p in path) for path, _ in _generator_refs(body)]
        if refs:
            raise InputError(f"{src}: {', '.join(refs)} read a generator, and the case declares none")
    if "loops" not in data:
        refs = [".".join(str(p) for p in path) for path, _ in _loop_refs(body)]
        if refs:
            raise InputError(f"{src}: {', '.join(refs)} read a loop, and the case declares none")
        result = _run(body, src)
        result.generated = generated
        return _measure(result)
    loops = _read_loops(data["loops"], body, src)
    result, reports = close(loops, lambda values: _run(_substitute(body, values), src))
    result.loops, result.generated = reports, generated
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
    balancing = {}
    for entity, accts in top.take_or_default("opening", {}, reason="the books open empty").value.items():
        balancing[entity] = _open(group, entity, accts, currency, f"{src}:opening.{entity}")
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
    # what rulebooks share: asset registers (read by income tax), distributors (a promote that
    # takes a waterfall's residual) and deals (an acquisition's marks, read by an operating plan),
    # each under the rulebook's name
    shared = {"currency": currency, "schemes": schemes, "registers": {}, "distributors": {}, "deals": {}}
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
        kind = r.take_or_default("kind", "cash", reason="a cut of cash movements").value
        if kind == "pnl":
            cuts[cname] = pnl_cut(group.book(r.take("entity")), schemes[r.take("scheme")], r.take("include"), name=cname)
        elif kind == "cash":
            cuts[cname] = cash_flow_cut(group.book(r.take("entity")), r.take("account"),
                                        schemes[r.take("scheme")], r.take("include"), name=cname)
        elif kind == "sum":
            # a view over cuts declared above, which may read different books (a company's CFADS less
            # a plan's capex, for one IRR): it posts nothing, so every part stays read where it is booked
            add = list(r.take("add"))
            subtract = list(r.take_or_default("subtract", [], reason="nothing taken away").value)
            for part in add + subtract:
                if part not in cuts:
                    raise InputError(f"{src}: cut '{cname}' sums '{part}', which is not a cut declared above")
            n = len(cuts[(add + subtract)[0]].values)
            cuts[cname] = Series(cname, [sum((cuts[a].values[t] for a in add), Fraction(0))
                                         - sum((cuts[b].values[t] for b in subtract), Fraction(0))
                                         for t in range(n)],
                                 frozenset().union(*(cuts[p].states for p in add + subtract)))
        else:
            raise InputError(f"{src}: cut '{cname}' has unknown kind '{kind}' (cash, pnl or sum)")
        r.finish()
    lens_values: Dict[str, Dict[str, Any]] = {}
    for lname, raw in top.take_or_default("lenses", {}, reason="no lens declared").value.items():
        r = Reader(raw, source=f"{src}:lenses.{lname}")
        kind = r.take("kind")
        if kind not in LENSES:
            raise InputError(f"{src}: lens '{lname}' has unknown kind '{kind}'")
        out = LENSES[kind](group, r, {"currency": currency, "schemes": schemes})
        r.finish()
        for sname, series in out["series"].items():
            cuts[f"{lname}.{sname}"] = series
        lens_values[lname] = out["values"]
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
        elif kind in ("npv", "xnpv"):
            rate = float(exact(r.take("rate")))
            dates = [_date(d) for d in r.take("dates")] if kind == "xnpv" else None
            through = r.take_or_default("through", None, reason="the whole stream is valued").value
            n = None if through is None else int(through) + 1

            def value(c=cut, k=kind, rate=rate, d=dates, n=n):
                values = cuts[c].values
                if n is not None and n > len(values):
                    raise InputError(f"{src}: measure through period {n - 1}, but the cut has {len(values)} periods")
                if k == "npv":
                    return M.npv(values[:n], rate)
                return M.xnpv(values[:n], d[:n], rate)
            pending[mname] = value
        elif kind == "irr":
            per_year = int(r.take_or_default("per_year", 1, reason="the stream's periods are years").value)
            pending[mname] = lambda c=cut, n=per_year: _annualised(M.irr(cuts[c].values), n)
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
    out.lenses, out.balancing = lens_values, {e: b for e, b in balancing.items() if b}
    return out


def _annualised(rate: float, n: int) -> float:
    """A per-period rate compounded over ``n`` periods; a :class:`~one_engine.measures.PickedRate`
    keeps its flag, with every root compounded the same way."""
    out = (1 + rate) ** n - 1
    if isinstance(rate, M.PickedRate):
        return M.PickedRate(out, [(1 + r) ** n - 1 for r in rate.roots])
    return out


def _open(group: Group, entity: str, accts: Mapping[str, Any], currency: str, where: str):
    """State one entity's opening balance sheet; return the balancing account and amount, if one is named."""
    stated, balancing = {}, None
    for acct, raw in accts.items():
        r = Reader(raw, source=f"{where}.{acct}")
        nature, value = Nature(r.take("nature")), r.take("value")
        r.finish()
        if value == "balancing":
            if balancing is not None:
                raise InputError(f"{where}: only one account may balance the opening, not {balancing[0]} and {acct}")
            balancing = (acct, nature)
        else:
            stated[acct] = (nature, exact(value))
    if balancing is not None:
        # debit-natured balances less credit-natured ones must be zero: the balancing account takes the rest
        net = sum((v if n.debit_natured else -v for n, v in stated.values()), exact(0))
        stated[balancing[0]] = (balancing[1], -net if balancing[1].debit_natured else net)
    for acct, (nature, value) in stated.items():
        group.open(entity, acct, nature, currency, value)
    return (balancing[0], stated[balancing[0]][1]) if balancing else None


def _measure(result: CaseResult) -> CaseResult:
    """Evaluate the accepted run's measures, in file order."""
    result.measures = {m: fn() for m, fn in result.pending_measures.items()}
    return result
