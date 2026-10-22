"""Workbooks as an oracle family: every formula cell a modeller's spreadsheet computed, run through the books.

The workbooks are Baghara's (Nour Energie's solar supply, the CAP 3Moissons
farms, the cattle integration and Haraz models), vendored with Ali's agreement
of 2026-10-13 and listed in ``MANIFEST.json`` with the path they came from.

The oracle
----------
``cases/reference/workbooks/<id>.xlsx`` (or ``.xlsm``), a copy of the workbook
as its author saved it, never opened for writing. Excel saves each formula's last computed
value beside the formula; those cached values are the workbook's answers.

Inputs
------
Everything the modeller typed, named mechanically so nothing is chosen:

* every number in a cell that holds no formula: ``<sheet>__<cell>``
  (``Hypotheses_Nour_3Moissons__C29``; the sheet name with accents dropped and
  every run of other characters made one ``_``);
* every number typed inside a formula: ``<sheet>__<cell>__n<k>``, k counting
  from 1 in the formula's reading order (``=M29*1.5`` gives ``...__N29__n1``
  = 1.5; ``=1-10%`` gives ``__n1`` = 1 and ``__n2`` = 0.1). A modeller's
  assumption hidden in a formula is still an assumption;
* every date typed in a cell, by the day count Excel stores for it (its
  serial: 30 June 2038 is 50586 in the 1900 date system), since a model's
  formulas compute with that number (the days in a period). A formula cell
  that saved a date is set aside by the oracle, as any cell that saved no
  plain number;
* every formula cell that reads another workbook (``=[2]PL!D27``; Excel
  numbers the linked files), by the value it imported, named as a typed cell:
  the linked file was not saved with this one, so nothing here can recompute
  it, and the workbook computed from what it imported;
* every run of two or more adjacent number cells in one row (typed or
  imported), also as a series: ``<sheet>__<first>_<last>`` (a row typed year
  by year).

Figures
-------
Every formula cell whose cached value is a number, grouped by row: a run of
adjacent formula cells in one row is one figure ``<sheet>.<first>:<last>``
(a series), a lone one ``<sheet>.<cell>``. A formula cell caching text, an
error or nothing is set aside by the oracle itself, with that reason.

A translation may read a series figure with one reading (a row of periods)
or with a list of readings, one per element (a row whose cells are different
quantities: an NPV under four sensitivities).

Comparison rule
---------------
Excel computes in IEEE doubles, so a figure agrees when it is within
``max(1e-9 x |value|, 1e-6)``: the drift of double arithmetic over a model's
chained formulas, with a micro-unit floor for amounts that cancel to zero.
A cell computed by Excel's ``IRR``, ``XIRR`` or ``RATE`` is held to ``1e-7``
instead: Excel iterates those "until the result is accurate within 0.00001
percent" (its documentation), which is the precision it saved.

Translation
-----------
``cases/workbooks/<id>.yaml``: the published keys (``workbook: <id>``,
``entity``, ``plant``, ``case`` with a formulas generator taking
``inputs: workbook``, ``figures``, ``not_compared``, ``notes``) plus
``variants`` as the hand golds have them: a sensitivity the workbook computes
beside its base case (capex +10%) is the same case with some formulas
replaced, and any reading, ``measure`` included, may name its ``variant``.

``not_compared`` takes a figure, or a whole sheet by its key (every figure on
it), with the reason, as a published translation sets aside a whole table.
Every other figure is read or fails as "not reported", so a workbook matches
only when every number it computed is reproduced or set aside with a reason;
the receipt counts how many were compared.

Planted error
-------------
The input named by ``plant``, times 1.01 (plus 1 when it is zero); the
workbook's saved values are untouched.
"""
from __future__ import annotations

import copy
import datetime
import hashlib
import pathlib
import re
import unicodedata
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

import openpyxl
from openpyxl.formula.tokenizer import Token, Tokenizer
from openpyxl.utils import column_index_from_string, get_column_letter
from openpyxl.utils.datetime import to_excel

from one_engine.inputs import load_yaml_strict
from one_engine.oracles import REFERENCE, Blocked, Check, rel_diff
from one_engine.oracles.golds import GoldFamily, _fmt, untraced
from one_engine.oracles.published import PublishedFamily, _echoes, _exact, untraced_published

FOLDER = "workbooks"
#: the workbook formats read: Excel's, with or without macros
EXTENSIONS = (".xlsx", ".xlsm")
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "workbooks"
#: Excel's documented precision for its iterative rate functions (0.00001 percent)
#: a reference into another workbook (``[2]PL!D27``): Excel numbers the linked files
EXTERNAL = re.compile(r"\[\d+\]")
ITERATIVE = re.compile(r"\b(X?IRR|RATE)\s*\(", re.I)
ITERATIVE_TOL = Fraction(1, 10 ** 7)
REL, FLOOR = Fraction(1, 10 ** 9), Fraction(1, 10 ** 6)


def sheet_key(title: str) -> str:
    """A sheet's name as an input prefix: accents dropped, other runs of characters one ``_``."""
    plain = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    key = re.sub(r"\W+", "_", plain).strip("_")
    return "_" + key if key[:1].isdigit() else key


def _literals(formula: str) -> List[Fraction]:
    """The numbers typed in a formula, in reading order; a percent sign divides by 100."""
    toks = Tokenizer(formula).items
    out = []
    for i, tok in enumerate(toks):
        if tok.type == Token.OPERAND and tok.subtype == Token.NUMBER:
            v = Fraction(tok.value)
            nxt = toks[i + 1] if i + 1 < len(toks) else None
            if nxt is not None and nxt.type == Token.OP_POST and nxt.value == "%":
                v /= 100
            out.append(v)
    return out


def _runs(cells: List[Tuple[int, int]]) -> List[List[Tuple[int, int]]]:
    """(row, col) cells grouped into runs of adjacent columns within a row."""
    runs: List[List[Tuple[int, int]]] = []
    for rc in sorted(cells):
        if runs and runs[-1][-1][0] == rc[0] and runs[-1][-1][1] + 1 == rc[1]:
            runs[-1].append(rc)
        else:
            runs.append([rc])
    return runs


def _ref(rc: Tuple[int, int]) -> str:
    return f"{get_column_letter(rc[1])}{rc[0]}"


@lru_cache(maxsize=None)
def read_workbook(path: pathlib.Path) -> Dict[str, Any]:
    """Inputs, figures, the oracle's own set-asides and each figure's formulas, from one workbook."""
    wf = openpyxl.load_workbook(path, data_only=False)
    wv = openpyxl.load_workbook(path, data_only=True)
    inputs: Dict[str, Any] = {}
    figures: Dict[str, Any] = {}
    aside: Dict[str, str] = {}
    formulas: Dict[str, List[str]] = {}
    for ws in wf.worksheets:
        key = sheet_key(ws.title)
        consts, fcells = [], {}
        for row in ws.iter_rows():
            for c in row:
                v = c.value
                if isinstance(v, str) and v.startswith("=") and EXTERNAL.search(v):
                    # it reads a workbook not saved with this one: nothing here can recompute it, so the
                    # value it imported is an input (text it imported, a label, is nothing)
                    got = wv[ws.title][c.coordinate].value
                    if isinstance(got, (int, float)) and not isinstance(got, bool):
                        inputs[f"{key}__{c.coordinate}"] = _exact(got)
                        consts.append((c.row, c.column))
                elif isinstance(v, str) and v.startswith("="):
                    for k, lit in enumerate(_literals(v), 1):
                        inputs[f"{key}__{c.coordinate}__n{k}"] = lit
                    fcells[(c.row, c.column)] = (v, wv[ws.title][c.coordinate].value)
                elif isinstance(v, (int, float)) and not isinstance(v, bool):
                    inputs[f"{key}__{c.coordinate}"] = _exact(v)
                    consts.append((c.row, c.column))
                elif isinstance(v, (datetime.datetime, datetime.date)):
                    # a typed date is the day count Excel stores (its serial, in the workbook's own
                    # date system), the number the model's formulas compute with
                    inputs[f"{key}__{c.coordinate}"] = _exact(to_excel(v, wf.epoch))
                    consts.append((c.row, c.column))
        for run in _runs(consts):
            if len(run) > 1:
                inputs[f"{key}__{_ref(run[0])}_{_ref(run[-1])}"] = [inputs[f"{key}__{_ref(rc)}"] for rc in run]
        numeric = [rc for rc, (_, val) in fcells.items() if isinstance(val, (int, float)) and not isinstance(val, bool)]
        for rc, (_, val) in fcells.items():
            if rc not in numeric:
                aside[f"{key}.{_ref(rc)}"] = f"the workbook saved no number here ({val!r})"
        for run in _runs(numeric):
            name = f"{key}.{_ref(run[0])}" + (f":{_ref(run[-1])}" if len(run) > 1 else "")
            vals = [_exact(fcells[rc][1]) for rc in run]
            figures[name] = vals if len(run) > 1 else vals[0]
            formulas[name] = [fcells[rc][0] for rc in run]
    return {"inputs": inputs, "figures": figures, "aside": aside, "formulas": formulas}


class WorkbookFamily(PublishedFamily):
    """Workbooks (see the module docstring)."""

    name = "workbook"
    key = "workbook"
    folder = FOLDER
    planted = "the input the translation names under 'plant', x 1.01 (+1 when it is zero)"
    tolerance = "max(1e-9 x |value|, 1e-6), Excel's double arithmetic; 1e-7 on IRR, XIRR and RATE cells"

    def __init__(self, translations: pathlib.Path = TRANSLATIONS, reference: pathlib.Path = REFERENCE):
        super().__init__(translations)
        self.reference = reference

    def _file(self, oid: str) -> pathlib.Path:
        """The workbook's file: ``.xlsx``, or ``.xlsm`` for one saved with macros (Bodmer's); the
        macros are never run, only the saved values read."""
        return next(p for p in (self.reference / FOLDER / f"{oid}{ext}" for ext in EXTENSIONS) if p.exists())

    def oracles(self) -> List[str]:
        return sorted(p.stem for ext in EXTENSIONS for p in (self.reference / FOLDER).glob(f"*{ext}"))

    def source(self, oid: str) -> str:
        return f"{FOLDER}/{self._file(oid).name}"

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """No inventory row is a workbook: they were vendored after the inventory was taken."""
        return None

    def _book(self, oid: str) -> Dict[str, Any]:
        return read_workbook(self._file(oid))

    def _load(self, oid: str):
        wb = self._book(oid)
        return wb, wb["formulas"]

    def _inputs(self, oracle):
        return dict(oracle["inputs"])

    def _wanted(self, oid, oracle):
        # every figure is the workbook's own answer: all scored, none may be set aside but by the oracle
        return dict(oracle["figures"]), {f: None for f in oracle["figures"]}, dict(oracle["aside"])

    def _within(self, fig, value, got, tol, canon):
        texts = tol[fig]
        allowed = ITERATIVE_TOL if any(ITERATIVE.search(t) for t in texts) else max(REL * abs(value), FLOOR)
        return abs(got - value) <= allowed, f"within {float(allowed):g}"

    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        """As :meth:`PublishedFamily.run`, with variants and per-element readings."""
        oracle, tol = self._load(oid)
        path = self.translation(oid)
        if path is None:
            raise Blocked(f"no translation yet (cases/{FOLDER}/{oid}.yaml)")
        tr = load_yaml_strict(path.read_text())
        unknown = sorted(set(tr) - {self.key, "entity", "plant", "case", "figures", "not_compared", "variants",
                                    "notes"})
        if unknown or tr.get(self.key) != oid:
            raise ValueError(f"{path.name}: keys {unknown} unknown, or {self.key} is not '{oid}'")
        inputs = self._inputs(oracle)
        planted = None
        if plant:
            name = tr["plant"]
            if not isinstance(inputs.get(name), Fraction):
                raise Blocked(f"plant names '{name}', which is not a numeric input of the workbook")
            v = inputs[name]
            inputs[name] = v * Fraction(101, 100) if v else Fraction(1)
            planted = f"{name}: {inputs[name]}"
        case = copy.deepcopy(tr["case"])
        fed = []
        for gname, g in (case.get("generators") or {}).items():
            if g.get("kind") == "formulas" and g.get("inputs") == self.key:
                g["inputs"] = {k: ([str(x) for x in v] if isinstance(v, list) else str(v)) for k, v in inputs.items()}
                fed.append(gname)
        if not fed:
            raise ValueError(f"{path.name}: no formulas generator takes 'inputs: {self.key}'")
        want, _, by_oracle = self._wanted(oid, oracle)
        specs = tr.get("figures") or {}
        aside = tr.get("not_compared") or {}
        # a set-aside names a figure, or a sheet by its key (every figure on it)
        def set_aside(fig: str) -> Optional[str]:
            return next((p for p in aside if fig == p or fig.startswith(p + ".")), None)
        wrong = sorted(set(specs) - set(want))
        idle = sorted(p for p in aside if not any(f == p or f.startswith(p + ".") for f in want))
        clash = sorted({f for f in specs if set_aside(f)} | ((set(specs) | set(aside)) & set(by_oracle)))
        if wrong or idle or clash:
            raise ValueError(f"{path.name}: figures not in the workbook {wrong}; set-asides matching nothing "
                             f"{idle}; read and set aside, or against the oracle's own set-asides {clash}")
        if not specs:
            raise Blocked("the translation compares no figure")
        flat = {f"{f}[{i}]": s for f, spec in specs.items()
                for i, s in enumerate(spec if isinstance(spec, list) else [spec])}
        echoes = sorted(f for f, s in flat.items() if _echoes(s, inputs, case))
        if echoes:
            raise ValueError(f"{path.name}: figures read straight from an input, which proves nothing: {echoes}")
        variants = tr.get("variants") or {}
        gate = untraced_published(tr["case"], flat) + untraced({"variants": variants})
        checks = [Check("translation traced", "every number in the case", "0 untraced",
                        "; ".join(gate) if gate else "0", not gate)]
        record: Dict[str, Any] = {"translation": f"cases/{FOLDER}/{path.name}",
                                  "translation_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "inputs": len(inputs), "fed_to": fed, "figures": len(want),
                                  "compared": len(specs), "cells": sum(len(v) if isinstance(v, list) else 1
                                                                       for f, v in want.items() if f in specs),
                                  "not_compared": {**aside, **by_oracle}}
        if planted:
            record["planted"] = planted

        from one_engine.cases import run_case
        results = {None: run_case(case)}
        for vname, v in variants.items():
            results[vname] = run_case(GoldFamily._variant(case, fed[0], vname, v))
        n = int(case["periods"])
        for fig, value in want.items():
            spec = specs.get(fig)
            if set_aside(fig) or fig in by_oracle:
                continue
            if spec is None:
                checks.append(Check(fig, "the figure", _fmt(value) if not isinstance(value, list)
                                    else f"series of {len(value)}", "not reported", False))
            elif isinstance(spec, list):
                checks.append(self._elements(fig, value, spec, results, tr["entity"], n, tol))
            else:
                checks.append(self._read_one(fig, value, spec, results, tr["entity"], n, tol))
        bad = [f"{k or 'base'}: {b}" for k, res in results.items() for b in res.failed_checks()]
        checks.append(Check("books", "every book and rulebook", "no failed check", "; ".join(bad) or "none", not bad))
        return checks, record

    def _read_one(self, fig, value, spec, results, entity, n, tol) -> Check:
        """One figure, one reading; a ``measure`` may name its ``variant``."""
        if "measure" in spec:
            if set(spec) - {"measure", "variant", "sign"}:
                raise ValueError(f"figure {fig}: a measure takes only variant and sign")
            got = Fraction(spec.get("sign", 1)) * Fraction(results[spec.get("variant")].measure(spec["measure"]))
            ok, rule = self._within(fig, value, got, tol, None)
            return Check(fig, rule, _fmt(value), _fmt(got), ok, rel=None if ok else rel_diff(value, got))
        return self._figure(fig, value, spec, {None: results[None], **results}, entity, n, tol, None)

    def _elements(self, fig, value, specs, results, entity, n, tol) -> Check:
        """A row of different quantities, one reading per cell."""
        if not isinstance(value, list) or len(specs) != len(value):
            return Check(fig, "length", f"{len(value) if isinstance(value, list) else 1} cells",
                         f"{len(specs)} readings", False)
        cells = [self._read_one(f"{fig}", w, s, results, entity, n, tol) for w, s in zip(value, specs)]
        bad = [(i, c) for i, c in enumerate(cells) if not c.ok]
        if bad:
            # the first cell that disagrees is shown; the size is the worst over all that do
            i, c = bad[0]
            rels = [c.rel for _, c in bad]
            return Check(fig, f"cell {i + 1}: {c.at}", c.expected, c.got, False,
                         rel=None if any(r is None for r in rels) else max(rels))
        return Check(fig, f"all {len(value)} cells", _fmt(value[-1]) + " at the last", "agree", True)
