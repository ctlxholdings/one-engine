"""The golds library as an oracle family: hand-derived small-business ladders.

The oracle
----------
``cases/reference/golds/<id>.yaml``, vendored unmodified from Ali's golds
library (``one-place/cases``). Each gold states its inputs by name, derives
the business period by period in prose (``passage``), and records every
stage of that derivation as a row of numbers (``trace``: physical drivers,
revenue, costs, EBITDA, depreciation, tax, working capital, capex, cash
flows), ending in free cash flow to the firm (``fcff``, the headline).

Translation
-----------
``cases/golds/<id>.yaml``. One file per gold, with these keys:

* ``gold``: the gold's id;
* ``entity``: the entity whose books the rows are read from;
* ``plant``: the gold input the planted run changes;
* ``case``: a one_engine case. Its numbers come from the gold: a generator
  of kind ``formulas`` (:mod:`one_engine.packs.tea`) with ``inputs: gold``
  receives every numeric gold input by name, and every amount the case
  needs is one of those inputs or a formula over them. The only numbers
  typed in the case are structural (``STRUCTURAL``: period indices, counts,
  60, 100 and 1000); any other is reported as a failed check, so a number
  fitted by hand cannot pass;
* ``trace``: for each gold trace row, where the engine reports it.

Where a row is read (``trace`` entries):

* ``{cut: name}``: a cut of the case (a P&L cut reads income less expense,
  so a cost row takes ``sign: -1``; a cash cut reads cash in less out);
* ``{balance: "a + b - c"}``: closing balances of the entity's accounts,
  each in its natural sense (an asset's debit balance, a liability's
  credit balance), added and subtracted as written;
* ``{opening: "..."}``: the same, at the opening of each period;
* ``{change: "..."}``: closing less opening (the change in working capital);
* ``{cumulative: name}``: a cut summed from the first period (accumulated
  depreciation);
* ``{record: kind.field}``: a rulebook's record of each period (the tax
  rulebook's loss pool: ``tax.losses_opening``, ``tax.loss_used``); a
  period with no record reads 0;
* ``{driver: name}``: a value the ``formulas`` generator computed. These are
  the gold's physical rows (loaves a year, trips, litres): the translation's
  arithmetic is compared with the gold's, but no ledger is involved, and the
  check's name says so (``driver ...``).

``{sum: [spec, ...]}`` adds rows read any of these ways (a row the gold
derives as a difference: an incremental revenue, the base run less a
variant's). Any of these may name a ``variant``: a "what if" of the gold (a higher tax
rate, an oven bought). ``variants`` (name -> ``{values: {name: formula}}``)
replaces formulas the case already computes and runs the case again; it
cannot add a piece the base run lacks.

``per: gen.output`` divides any row by a value the ``formulas`` generator
computed (a gold input by name): a row the gold states per head or per unit
(FCFF per worker). ``times: gen.output`` multiplies by one instead (the tax
a lost deduction would have saved: the expired losses times the rate). The
factor is the gold's own input, so nothing is typed.

``{carried_in: "a + b"}`` reads the balances carried into the gold's first
period (the gold's "opening injection" rows: that amount in the first
period, nil after); with ``hold: true``, the same amount every period (an
"opening level" row).

Warm-up
-------
A gold whose business is already running at its first period ("the pre-
horizon months are taken to be steady state") carries working-capital
balances in that only its own history explains: claims billed and not yet
paid, retentions held a year. ``warm_up: W`` runs the case W periods longer,
starting the business from nothing W periods before the gold's first
period, so the books build those balances themselves; every row is then read
from period W (cumulative rows sum from there). Every ``periods`` in the case
must be the gold's plus W (that is checked, so a ``periods`` is never a
number the gate has to trust), and the formulas generator receives
``warm_up`` as an input beside the gold's.

Every trace row is compared; a row the translation does not map is a failed
check ("not reported"), never a skip. One exception, listed in the receipt:
``labels`` (row -> reason) names a calendar row (``fiscal_year: [2026,
2027, ...]``) that is a label of the period, not a quantity of the business.
Only a row of consecutive whole numbers qualifies.

Checks, per oracle
------------------
* ``translation traced``: no number in the case outside ``STRUCTURAL``;
* one check per trace row: every period within 0.5 of the gold (the
  library's own tolerance, ``spec/GRAPH.yaml`` ``oracle.tolerance``: the
  currency is in whole units); the first period that differs is named;
* ``fcff``: the headline, the same way;
* ``books``: the integrity checks of every book and each rulebook's checks.

Planted error
-------------
The gold input named by ``plant``, times 1.01 (plus 1 when it is zero),
given to the translation; the gold's rows are untouched.
"""
from __future__ import annotations

import ast
import copy
import hashlib
import pathlib
import re
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from one_engine.inputs import load_yaml_strict
from one_engine.oracles import REFERENCE, Blocked, Check, Family

FOLDER = "golds"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "golds"
#: the library's ruler: amounts are whole currency units, so more than half a unit is a different claim
ATOL = Fraction(1, 2)
#: numbers a translation may type: period indices and counts, and the unit conversions 60 (minutes an
#: hour), 100 and 1000. A case's ``periods`` is not among them: it is checked against the gold's grid
STRUCTURAL = set(range(0, 25)) | {60, 100, 1000}
_TERM = re.compile(r"\s*([+-]?)\s*([A-Za-z_][\w.]*)")


@lru_cache(maxsize=None)
def _gold(oid: str) -> Dict[str, Any]:
    return yaml.safe_load((REFERENCE / FOLDER / f"{oid}.yaml").read_text())


def gold_inputs(gold: Mapping[str, Any]) -> Dict[str, Any]:
    """The gold's numeric inputs by name (a value, or ``{value: ...}``); text inputs are descriptions."""
    out = {}
    for k, v in (gold.get("inputs") or {}).items():
        val = v.get("value") if isinstance(v, dict) else v
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            out[k] = val
    return out


def _numbers(node: Any, path: str = "") -> List[Tuple[str, Any]]:
    """Every number typed in ``node`` (including inside formula text), with where it is."""
    out: List[Tuple[str, Any]] = []
    if isinstance(node, Mapping):
        for k, v in node.items():
            out += _numbers(v, f"{path}.{k}" if path else str(k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out += _numbers(v, f"{path}[{i}]")
    elif isinstance(node, bool) or node is None:
        pass
    elif isinstance(node, (int, float)):
        out.append((path, node))
    elif isinstance(node, str) and path.split(".")[-1] not in ("kind", "type") and any(c.isdigit() for c in node):
        try:
            tree = ast.parse(node, mode="eval")
        except SyntaxError:
            return out
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)) and not isinstance(n.value, bool):
                out.append((f"{path} ({node!r})", n.value))
    return out


def untraced(case: Mapping[str, Any]) -> List[str]:
    """The numbers of a translated case that are not structural: each entered from the author's head."""
    return [f"{where} = {v}" for where, v in _numbers(case)
            if v not in STRUCTURAL and where.split(".")[-1] != "periods"]


def _terms(expr: str) -> List[Tuple[int, str]]:
    """``"a + b - c"`` as ``[(1, a), (1, b), (-1, c)]``; refuses anything else."""
    out, pos = [], 0
    while pos < len(expr.strip()):
        m = _TERM.match(expr, pos)
        if not m or (out and not m.group(1)):
            raise ValueError(f"cannot read balance expression {expr!r}")
        out.append((-1 if m.group(1) == "-" else 1, m.group(2)))
        pos = m.end()
        while pos < len(expr) and expr[pos] == " ":
            pos += 1
    return out


def _fmt(x: Fraction) -> str:
    return f"{float(x):,.4f}".rstrip("0").rstrip(".")


class GoldFamily(Family):
    name = "golds"
    planted = "the gold input the translation names under 'plant', x 1.01 (+1 when it is zero)"
    tolerance = "every trace row and the fcff headline within 0.5 currency units per period (spec/GRAPH.yaml oracle.tolerance)"

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        #: the folder of translations (a test points it at a planted copy)
        self.translations = translations

    def oracles(self) -> List[str]:
        return sorted(p.stem for p in (REFERENCE / FOLDER).glob("*.yaml"))

    def source(self, oid: str) -> str:
        return f"{FOLDER}/{oid}.yaml"

    def translation(self, oid: str) -> Optional[pathlib.Path]:
        p = self.translations / f"{oid}.yaml"
        return p if p.exists() else None

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        path = self.translation(oid)
        if path is None:
            raise Blocked(f"no translation yet (cases/golds/{oid}.yaml)")
        gold = _gold(oid)
        tr = load_yaml_strict(path.read_text())
        unknown = sorted(set(tr) - {"gold", "entity", "plant", "case", "trace", "variants", "labels", "notes",
                                    "warm_up"})
        if unknown or tr.get("gold") != oid:
            raise ValueError(f"{path.name}: keys {unknown} unknown, or gold is not '{oid}'")
        inputs = gold_inputs(gold)
        n = int(gold["grid"]["periods"])
        warm = tr.get("warm_up", 0)
        if not isinstance(warm, int) or isinstance(warm, bool) or warm < 0:
            raise ValueError(f"{path.name}: warm_up is a whole number of periods, not {warm!r}")
        wrong = [f"{w} = {v}" for w, v in _numbers(tr["case"]) if w.split(".")[-1] == "periods" and v != n + warm]
        if wrong:
            raise ValueError(f"{path.name}: periods must be the gold's {n} plus warm_up {warm}: {wrong}")
        planted = None
        if plant:
            name = tr["plant"]
            if name not in inputs:
                raise Blocked(f"plant names '{name}', which is not a numeric input of the gold")
            v = Fraction(repr(inputs[name])) if isinstance(inputs[name], float) else Fraction(inputs[name])
            inputs = {**inputs, name: str(v * Fraction(101, 100) if v else Fraction(1))}
            planted = f"{name}: {inputs[name]}"
        case = copy.deepcopy(tr["case"])
        fed = []
        for gname, g in (case.get("generators") or {}).items():
            if g.get("kind") == "formulas" and g.get("inputs") == "gold":
                if warm and "warm_up" in inputs:
                    raise ValueError(f"{path.name}: the gold has an input named warm_up; it cannot be fed both")
                g["inputs"] = {**inputs, "warm_up": warm} if warm else inputs
                fed.append(gname)
        if not fed:
            raise ValueError(f"{path.name}: no formulas generator takes 'inputs: gold'")
        record = {"translation": f"cases/golds/{path.name}",
                  "translation_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                  "gold_inputs": len(inputs), "fed_to": fed}
        if planted:
            record["planted"] = planted
        variants = tr.get("variants") or {}
        gate = untraced({"case": tr["case"], "variants": variants})
        checks = [Check("translation traced", "every number in the case", "0 untraced",
                        "; ".join(gate) if gate else "0", not gate)]

        from one_engine.cases import run_case       # the engine, imported only when a gold runs
        results = {None: run_case(case)}
        for vname, v in variants.items():
            results[vname] = run_case(self._variant(case, fed[0], vname, v))
        rows = dict(gold.get("trace") or {})
        mapping = tr.get("trace") or {}
        labels = tr.get("labels") or {}
        for row, reason in labels.items():
            want = rows.pop(row, None)
            # a label row (a calendar year) is no quantity of the business: it is listed, not compared,
            # and only a run of consecutive whole numbers qualifies
            if want is None or row in mapping or any(b - a != 1 for a, b in zip(want, want[1:])):
                raise ValueError(f"{path.name}: label '{row}' is not a gold row of consecutive years, or is also mapped")
            record.setdefault("labels_not_compared", {})[row] = reason
        for row, want in rows.items():
            spec = mapping.get(row)
            if spec is None:
                checks.append(Check(row, "every period", "a mapped row", "not reported", False))
                continue
            got = self._read(results, tr["entity"], spec, n, warm)
            label = f"driver {row}" if "driver" in spec else row
            checks.append(self._compare(label, want, got))
        extra = sorted(set(mapping) - set(rows))
        if extra:
            checks.append(Check("trace map", "rows", "only gold rows", f"unknown rows {extra}", False))
        fcff = mapping.get("fcff")
        if fcff is not None and gold.get("fcff") is not None:
            checks.append(self._compare("fcff (headline)", gold["fcff"],
                                        self._read(results, tr["entity"], fcff, n, warm)))
        bad = [f"{k or 'base'}: {b}" for k, res in results.items() for b in res.failed_checks()]
        checks.append(Check("books", "every book and rulebook", "no failed check", "; ".join(bad) or "none", not bad))
        return checks, record

    @staticmethod
    def _variant(case: Mapping[str, Any], gen: str, name: str, spec: Mapping[str, Any]) -> Dict[str, Any]:
        """The case with some of the generator's formulas replaced: a gold's "what if" rows.

        A variant may only replace a formula the case already has, so it
        changes a value and never adds a piece the base run does not have.
        """
        values = spec.get("values") or {}
        if set(spec) - {"values"} or not values:
            raise ValueError(f"variant '{name}': give values, the formulas it replaces")
        out = copy.deepcopy(case)
        base = out["generators"][gen]["values"]
        missing = sorted(set(values) - set(base))
        if missing:
            raise ValueError(f"variant '{name}' replaces {missing}, which the case does not compute")
        base.update(values)
        return out

    @staticmethod
    def _compare(name: str, want: List[Any], got: List[Fraction]) -> Check:
        if len(got) != len(want):
            return Check(name, "length", f"{len(want)} periods", f"{len(got)} periods", False)
        for t, (w, g) in enumerate(zip(want, got)):
            w = Fraction(repr(w)) if isinstance(w, float) else Fraction(w)
            if abs(g - w) > ATOL:
                return Check(name, f"t={t}", _fmt(w), _fmt(g), False)
        return Check(name, f"all {len(want)} periods", _fmt(Fraction(repr(want[-1])) if isinstance(want[-1], float)
                                                           else Fraction(want[-1])) + " at the last",
                     _fmt(got[-1]) + " at the last", True)

    @staticmethod
    def _read(results, entity: str, spec: Mapping[str, Any], n: int, warm: int = 0) -> List[Fraction]:
        """One trace row as the engine reports it: the gold's ``n`` periods, after ``warm`` of warm-up."""
        sign = Fraction(spec.get("sign", 1))
        if "sum" in spec:
            if "per" in spec or "times" in spec:
                raise ValueError("per and times scale one row; put them on a part of the sum")
            parts = [GoldFamily._read(results, entity, s, n, warm) for s in spec["sum"]]
            return [sign * sum(col, Fraction(0)) for col in zip(*parts)]
        result = results[spec.get("variant")]
        if "carried_in" in spec:
            book = result.group.book(entity)
            first = sum((s * book.opening(a, warm) for s, a in _terms(spec["carried_in"])), Fraction(0))
            return [sign * (first if spec.get("hold") or t == 0 else Fraction(0)) for t in range(n)]
        if "cut" in spec:
            vals = list(result.cut(spec["cut"]).values)
        elif "cumulative" in spec:
            vals, run = [Fraction(0)] * warm, Fraction(0)
            for x in list(result.cut(spec["cumulative"]).values)[warm:]:
                run += x
                vals.append(run)
        elif "record" in spec:
            kind, _, field = spec["record"].partition(".")
            recs = result.records(kind)
            vals = [recs[t][field] if t in recs else Fraction(0) for t in range(n + warm)]
        elif "driver" in spec:
            gen, _, out = spec["driver"].rpartition(".")
            v = result.generated[gen or next(iter(result.generated))][out]
            vals = list(v) if isinstance(v, list) else [v] * (n + warm)
        else:
            kind = next(k for k in ("balance", "opening", "change") if k in spec)
            book = result.group.book(entity)
            terms = _terms(spec[kind])

            def at(t: int, opening: bool) -> Fraction:
                return sum((s * (book.opening(a, t) if opening else book.balance(a, t)) for s, a in terms), Fraction(0))
            if kind == "balance":
                vals = [at(t, False) for t in range(n + warm)]
            elif kind == "opening":
                vals = [at(t, True) for t in range(n + warm)]
            else:
                vals = [at(t, False) - at(t, True) for t in range(n + warm)]
        for key in ("per", "times"):
            if key in spec:
                gen, _, out = spec[key].rpartition(".")
                f = result.generated[gen or next(iter(result.generated))][out]
                f = [Fraction(f[t] if isinstance(f, list) else f) for t in range(len(vals))]
                vals = [Fraction(x) / f[t] if key == "per" else Fraction(x) * f[t] for t, x in enumerate(vals)]
        vals = vals[warm:]
        return [sign * Fraction(x) for x in vals]

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """Claim the inventory rows of the hand golds (``one-place/cases/<id>.yaml``)."""
        path = row.get("path", "")
        m = re.search(r"one-place/cases/([a-z0-9_]+)\.yaml$", path)
        if m and (REFERENCE / FOLDER / f"{m.group(1)}.yaml").exists():
            return m.group(1)
        return None
