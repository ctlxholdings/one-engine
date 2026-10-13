"""Published cases as an oracle family: figures a third party printed, run through the books.

The oracle
----------
``cases/reference/published/<id>.yaml``, vendored unmodified from Ali's golds
library (``one-place/cases/golds``). Each file transcribes one published
source (an annual report, an extension-service budget, a textbook chapter, a
feasibility study): ``inputs`` are the drivers the source prints, ``expected``
the figures it prints as its answers, ``tolerance`` the source's own rounding
with a written reason, and ``mapping`` the figures the library itself scores
(its headline claims).

Unlike a hand gold, a published case has no common shape: ``inputs`` and
``expected`` are trees the transcriber laid out to follow the source (costs by
nature, a trial balance by account, a series by year). This family reads them
mechanically:

* **inputs.** Every number in ``inputs`` (a bare number, or the ``value`` of a
  ``{value: ...}`` node) is handed to the translation's ``formulas`` generator
  under a name: the shortest tail of its path that no other input shares, the
  keys joined by ``__`` (``costs.emballages_et_EPI`` and
  ``movements.emballages_et_EPI`` both exist, so both keep their head; a key
  that is not a Python name is made one: ``2016`` becomes ``_2016``; a
  position in a list joins the key holding the list, so the amount of the
  first journal line is ``journal_0__amount``). A list of numbers is handed
  over as a series. Text is description, not input.
* **figures.** Every number in ``expected`` is a figure: a bare number, a
  ``value``, a list of numbers (a series, one figure), a ``values`` list, or a
  text that is only a number as printed (``"$41,590"``, ``"13%"``, read as
  41590 and 13). A figure is named by its dotted path (``benefice``,
  ``balance_sheet_usd.assets.cash``; a ``value`` node by the node's path).

Translation
-----------
``cases/published/<id>.yaml``, with these keys:

* ``published``: the oracle's id; ``entity``: whose books are read;
* ``plant``: the input (by its name above) the planted run changes;
* ``case``: a one_engine case. As for the hand golds
  (:mod:`one_engine.oracles.golds`), a ``formulas`` generator with
  ``inputs: published`` receives the inputs, and any number typed in the case
  outside ``STRUCTURAL`` fails the ``translation traced`` check;
* ``figures``: figure path -> where the engine reports it. Any reading of
  :meth:`GoldFamily._read <one_engine.oracles.golds.GoldFamily._read>`
  (``cut``, ``balance``, ``opening``, ``change``, ``cumulative``, ``record``,
  ``driver``, ``sum``, ``ratio``, ``per``, ``times``, ``carried_in``, and ``running``
  on any of them), plus:

  - ``at: t``: a single figure is the reading at period ``t``; ``total: true``
    sums the reading over every period instead. A one-period case needs
    neither;
  - ``periods: [t, ...]``: a series figure's elements are these periods of the
    reading (default: every period, and the lengths must agree);
  - ``scale``: the source prints in thousands (or any power of ten; 0.01 for
    a rate printed as a percent): the engine's amount is divided by it before
    comparing, so the tolerance keeps the source's own unit;
  - ``{measure: name}`` instead of a reading: a single figure one of the
    case's ``measures`` computes (an IRR, an NPV at the source's stated
    rate), with ``scale`` and ``sign`` allowed;
* ``not_compared``: figure path, or the path of a whole table, -> the reason
  it is not compared (a prior-year column the case does not model, the
  distractors of a multiple-choice question, a physical statistic with no
  money in it). Each is listed in the receipt with its reason. A figure the
  library's own ``mapping`` scores can never be set aside this way;
* ``notes``: anything a reader needs.

A ``driver`` reading that only repeats an input (the input's own name, or a
formula that is nothing but that name) is refused: the figure would be
compared with itself.

Every figure is either compared or set aside with a reason: a figure the
translation neither maps nor sets aside is a failed check ("not reported").

Checks, per oracle
------------------
* ``translation traced``: no number in the case (or in a figure's reading)
  outside ``STRUCTURAL``;
* one check per compared figure, inside the oracle's tolerance: ``abs``, or
  ``rel`` times the figure when that is wider (``max(abs, rel * |figure|)``,
  as the library's runner reads it); a figure the library maps to a quantity
  name takes ``tolerance.quantities.<name>`` instead; a series figure, every
  element;
* ``books``: the integrity checks of every book and each rulebook's checks.

A case whose ``tolerance`` is missing, or carries no ``why``, is blocked: the
library's own rule is that it does not run.

Planted error
-------------
The input named by ``plant``, times 1.01 (plus 1 when it is zero), given to the
translation; the published figures are untouched.
"""
from __future__ import annotations

import copy
import hashlib
import keyword
import pathlib
import re
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from one_engine.inputs import load_yaml_strict
from one_engine.oracles import REFERENCE, Blocked, Check, Family
from one_engine.oracles.golds import STRUCTURAL, GoldFamily, _fmt, _numbers

FOLDER = "published"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "published"
#: a figure's reading may also divide by a power of ten: the source's printed unit (thousands, millions;
#: 0.01 for a rate the source prints as a percent)
SCALES = {Fraction(10) ** k for k in range(-4, 10) if k}
#: a text figure is a number as printed: an optional currency sign, digits with thousands separators, a percent
_PRINTED = re.compile(r"^\s*(-?)\s*[$£€]?\s*(-?)([0-9][0-9,]*(?:\.[0-9]+)?)\s*%?\s*$")


@lru_cache(maxsize=None)
def _oracle(oid: str) -> Dict[str, Any]:
    return yaml.safe_load((REFERENCE / FOLDER / f"{oid}.yaml").read_text())


def _exact(v: Any) -> Fraction:
    return Fraction(repr(v)) if isinstance(v, float) else Fraction(v)


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _printed(v: Any) -> Optional[Fraction]:
    """A text that is only a number as printed (``"$2,394.98"``, ``"13%"``), as that number; else None."""
    if not isinstance(v, str):
        return None
    m = _PRINTED.match(v)
    if not m or (m.group(1) and m.group(2)):
        return None
    x = Fraction(m.group(3).replace(",", ""))
    return -x if (m.group(1) or m.group(2)) else x


def _leaves(node: Any, path: Tuple[Any, ...], text: bool) -> List[Tuple[Tuple[Any, ...], Any]]:
    """The numbers of a tree with their key paths; a ``value`` node is its value, a number list one leaf.

    With ``text``, a text that is only a printed number counts too (figures);
    inputs take real numbers only.
    """
    out: List[Tuple[Tuple[str, ...], Any]] = []
    if isinstance(node, Mapping):
        for k, v in node.items():
            sub = path if k in ("value", "values") and isinstance(node, Mapping) else path + (str(k),)
            out += _leaves(v, sub, text)
    elif isinstance(node, list):
        if node and all(_is_number(x) for x in node):
            out.append((path, [_exact(x) for x in node]))
        else:
            for i, v in enumerate(node):
                out += _leaves(v, path + (i,), text)     # an int: a position in a list, not a key
    elif _is_number(node):
        out.append((path, _exact(node)))
    elif text and _printed(node) is not None:
        out.append((path, _printed(node)))
    return out


def _ident(key: str) -> str:
    """A key as a Python name: other characters become ``_``; a leading digit or a keyword gets ``_``."""
    s = re.sub(r"\W", "_", key)
    return "_" + s if (not s or s[0].isdigit() or keyword.iskeyword(s)) else s


def published_inputs(oracle: Mapping[str, Any]) -> Dict[str, Any]:
    """The oracle's numeric inputs by name (see the module docstring): a number, or a list of numbers."""
    leaves = _leaves(oracle.get("inputs") or {}, (), text=False)
    paths = []
    for p, _ in leaves:
        # a list position joins the key holding the list: journal[0].amount is journal_0 / amount
        keys: List[str] = []
        for k in p:
            if isinstance(k, int) and keys:
                keys[-1] += f"_{k}"
            else:
                keys.append(_ident(str(k)))
        paths.append(tuple(keys))
    out: Dict[str, Any] = {}
    for (_, v), p in zip(leaves, paths):
        for n in range(1, len(p) + 1):
            tail = p[-n:]
            if sum(1 for q in paths if q[-n:] == tail) == 1:
                break
        name = "__".join(tail)
        if name in out:                 # two equal paths: the tree repeats a key, which YAML forbids
            raise ValueError(f"two inputs both named {name!r}")
        out[name] = v
    return out


def figures(oracle: Mapping[str, Any]) -> Dict[str, Any]:
    """Every published figure by dotted path (see the module docstring): a number, or a series."""
    out = {}
    for path, v in _leaves(oracle.get("expected") or {}, (), text=True):
        out[".".join(str(k) for k in path)] = v
    return out


class PublishedFamily(Family):
    """The published cases (see the module docstring).

    The reading machinery is shared with other families whose oracles state
    figures in a different layout (:mod:`one_engine.oracles.esco`): a subclass
    sets ``key`` and ``folder`` and overrides :meth:`_load`, :meth:`_inputs`,
    :meth:`_wanted` and :meth:`_within`.
    """

    name = "published"
    #: the translation's id key, the generator's ``inputs:`` marker, and the translations' folder name
    key = "published"
    #: the oracles' folder under ``cases/reference``
    folder = FOLDER
    planted = "the input the translation names under 'plant', x 1.01 (+1 when it is zero)"
    tolerance = ("every compared figure within the case's own tolerance: max(abs, rel x |figure|), "
                 "or tolerance.quantities for a figure the library maps to a quantity")

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        #: the folder of translations (a test points it at a planted copy)
        self.translations = translations

    def oracles(self) -> List[str]:
        return sorted(p.stem for p in (REFERENCE / self.folder).glob("*.yaml"))

    def source(self, oid: str) -> str:
        return f"{self.folder}/{oid}.yaml"

    def translation(self, oid: str) -> Optional[pathlib.Path]:
        p = self.translations / f"{oid}.yaml"
        return p if p.exists() else None

    @staticmethod
    def _allowed(tol: Mapping[str, Any], canon: Optional[str], value: Fraction) -> Fraction:
        """The widest gap the source's own rounding explains, for one figure (element)."""
        q = (tol.get("quantities") or {}).get(canon) if canon else None
        t = q if q is not None else tol
        return max(_exact(t.get("abs", 0)), _exact(t.get("rel", 0)) * abs(value))

    # ── what differs between families ──────────────────────────────────────
    def _load(self, oid: str) -> Tuple[Mapping[str, Any], Any]:
        """The oracle and its tolerance; a case without a tolerance and a reason does not run."""
        oracle = _oracle(oid)
        tol = oracle.get("tolerance")
        if not isinstance(tol, Mapping) or not str(tol.get("why", "")).strip():
            raise Blocked("the case declares no tolerance with a reason, so the library does not run it")
        return oracle, tol

    def _inputs(self, oracle: Mapping[str, Any]) -> Dict[str, Any]:
        return published_inputs(oracle)

    def _wanted(self, oid: str, oracle: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
        """The figures (path -> value), the library's scored ones (path -> canon name), and any the
        oracle itself sets aside (path -> its reason)."""
        mapped = {str(k): (v.get("canon") if isinstance(v, Mapping) else v)
                  for k, v in (oracle.get("mapping") or {}).items()}
        return figures(oracle), mapped, {}

    def _within(self, fig: str, value: Fraction, got: Fraction, tol: Any, canon: Optional[str]) -> Tuple[bool, str]:
        """Whether ``got`` reproduces ``value``, and the rule applied, in words."""
        allowed = self._allowed(tol, canon, value)
        return abs(got - value) <= allowed, f"within {_fmt(allowed)}"

    # ── shared ───────────────────────────────────────────────────────────────
    def run(self, oid: str, plant: bool = False) -> Tuple[List[Check], Dict[str, Any]]:
        # the oracle first: one that cannot be compared at all (no tolerance, no figure) says so,
        # rather than naming a translation that would not change its verdict
        oracle, tol = self._load(oid)
        path = self.translation(oid)
        if path is None:
            raise Blocked(f"no translation yet (cases/{self.key}/{oid}.yaml)")
        tr = load_yaml_strict(path.read_text())
        unknown = sorted(set(tr) - {self.key, "entity", "plant", "case", "figures", "not_compared", "notes"})
        if unknown or tr.get(self.key) != oid:
            raise ValueError(f"{path.name}: keys {unknown} unknown, or {self.key} is not '{oid}'")
        inputs = self._inputs(oracle)
        planted = None
        if plant:
            name = tr["plant"]
            if not isinstance(inputs.get(name), Fraction):
                raise Blocked(f"plant names '{name}', which is not a numeric input of the case")
            v = inputs[name]
            inputs = {**inputs, name: str(v * Fraction(101, 100) if v else Fraction(1))}
            planted = f"{name}: {inputs[name]}"
        case = copy.deepcopy(tr["case"])
        fed = []
        for gname, g in (case.get("generators") or {}).items():
            if g.get("kind") == "formulas" and g.get("inputs") == self.key:
                g["inputs"] = {k: ([str(x) for x in v] if isinstance(v, list) else str(v)) for k, v in inputs.items()}
                fed.append(gname)
        if not fed:
            raise ValueError(f"{path.name}: no formulas generator takes 'inputs: {self.key}'")

        want, mapped, by_oracle = self._wanted(oid, oracle)
        specs = tr.get("figures") or {}
        aside = tr.get("not_compared") or {}
        clash = sorted(set(by_oracle) & (set(specs) | set(aside)))
        if clash:
            raise ValueError(f"{path.name}: figures the oracle itself sets aside are also read or set aside: {clash}")
        aside = {**aside, **by_oracle}
        # a figure is set aside by its own path or by a table above it
        def set_aside(fig: str) -> Optional[str]:
            return next((p for p in aside if fig == p or fig.startswith(p + ".")), None)
        wrong = sorted(set(specs) - set(want))
        idle = sorted(p for p in aside if not any(f == p or f.startswith(p + ".") for f in want))
        both = sorted(f for f in specs if set_aside(f))
        # a scored path is the figure itself, or a table whose figures are all scored; a figure that
        # has its own value keeps only that value scored, not the comparatives printed under it
        headline = sorted(f for f in want if set_aside(f)
                          and any(f == m or (f.startswith(m + ".") and m not in want) for m in mapped))
        if wrong or idle or both or headline:
            raise ValueError(f"{path.name}: figures not in the case {wrong}; set-asides matching nothing {idle}; "
                             f"figures both read and set aside {both}; scored figures set aside {headline}")
        echoes = sorted(f for f, spec in specs.items() if _echoes(spec, inputs, case))
        if echoes:
            raise ValueError(f"{path.name}: figures read straight from an input, which proves nothing: {echoes}")
        compared = [f for f in want if f in specs]
        if not compared:
            raise Blocked("the translation compares no figure")
        record: Dict[str, Any] = {"translation": f"cases/{self.key}/{path.name}",
                                  "translation_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                                  "inputs": len(inputs), "fed_to": fed, "figures": len(want),
                                  "compared": len(compared),
                                  "not_compared": {p: aside[p] for p in aside}}
        if planted:
            record["planted"] = planted
        gate = untraced_published(tr["case"], specs)
        checks = [Check("translation traced", "every number in the case", "0 untraced",
                        "; ".join(gate) if gate else "0", not gate)]

        from one_engine.cases import run_case       # the engine, imported only when a case runs
        results = {None: run_case(case)}
        n = int(case["periods"])
        for fig, value in want.items():
            if set_aside(fig):
                continue
            spec = specs.get(fig)
            if spec is None:
                checks.append(Check(fig, "the figure", _show(value), "not reported", False))
                continue
            canon = next((c for m, c in mapped.items() if fig == m or fig.startswith(m + ".")), None)
            checks.append(self._figure(fig, value, spec, results, tr["entity"], n, tol, canon))
        bad = [f"{k or 'base'}: {b}" for k, res in results.items() for b in res.failed_checks()]
        checks.append(Check("books", "every book and rulebook", "no failed check", "; ".join(bad) or "none", not bad))
        return checks, record

    def _figure(self, fig: str, value: Any, spec: Mapping[str, Any], results, entity: str, n: int,
                tol: Mapping[str, Any], canon: Optional[str]) -> Check:
        """One published figure against the engine's reading of it."""
        extra = {"at", "total", "periods", "scale"}
        if "measure" in spec:
            # a scalar the case's measures compute from a cut (an IRR, an NPV at a stated rate); measures
            # are float, outside the exact kernel, so the value is taken exactly as the float it is
            if set(spec) - {"measure", "scale", "sign"}:
                raise ValueError(f"figure {fig}: a measure takes only scale and sign")
            m = results[None].measure(spec["measure"])
            reading = [Fraction(spec.get("sign", 1)) * Fraction(m)]
        else:
            reading = GoldFamily._read(results, entity, {k: v for k, v in spec.items() if k not in extra}, n)
        scale = Fraction(spec.get("scale", 1))
        reading = [x / scale for x in reading]
        label = f"driver {fig}" if "driver" in spec else fig
        if isinstance(value, list):
            idx = spec.get("periods", list(range(len(reading))))
            if len(idx) != len(value):
                return Check(label, "length", f"{len(value)} elements", f"{len(idx)} periods read", False)
            for i, (w, t) in enumerate(zip(value, idx)):
                if not self._within(fig, w, reading[t], tol, canon)[0]:
                    return Check(label, f"element {i} (t={t})", _fmt(w), _fmt(reading[t]), False)
            return Check(label, f"all {len(value)} elements", _fmt(value[-1]) + " at the last",
                         _fmt(reading[idx[-1]]) + " at the last", True)
        if spec.get("total"):
            got, at = sum(reading, Fraction(0)), "over every period"
        elif "at" in spec:
            got, at = reading[int(spec["at"])], f"t={spec['at']}"
        elif len(reading) == 1:
            got, at = reading[0], "the period"
        else:
            raise ValueError(f"figure {fig}: a single figure from a {len(reading)}-period reading needs at or total")
        ok, rule = self._within(fig, value, got, tol, canon)
        return Check(label, f"{at}, {rule}", _fmt(value), _fmt(got), ok)

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """Claim the inventory rows of the published cases (``cases/golds/<id>.yaml``, encoding g_published_case)."""
        if row.get("encoding_id") != "g_published_case":
            return None
        m = re.search(r"cases/golds/([A-Za-z0-9_]+)\.yaml$", row.get("path", ""))
        if m and (REFERENCE / FOLDER / f"{m.group(1)}.yaml").exists():
            return m.group(1)
        return None


def _echoes(spec: Any, inputs: Mapping[str, Any], case: Mapping[str, Any]) -> bool:
    """Whether a reading is a ``driver`` that only repeats an input (directly, or through a formula that
    is the input's bare name): the figure would then be compared with itself."""
    if isinstance(spec, Mapping) and "sum" in spec:
        return any(_echoes(s, inputs, case) for s in spec["sum"])
    if not isinstance(spec, Mapping) or "driver" not in spec:
        return False
    gen, _, out = str(spec["driver"]).rpartition(".")
    gens = case.get("generators") or {}
    values = (gens.get(gen) or next(iter(gens.values()), {}) or {}).get("values") or {}
    text = values.get(out)
    text = text.get("each_period") if isinstance(text, Mapping) else text
    return out in inputs or (isinstance(text, str) and text.strip() in inputs)


def _show(v: Any) -> str:
    return f"series of {len(v)}" if isinstance(v, list) else _fmt(v)


def untraced_published(case: Mapping[str, Any], specs: Mapping[str, Any]) -> List[str]:
    """Numbers typed in the case or in a figure's reading that are not structural.

    A figure's reading may also carry a ``scale`` that is a power of ten (the
    source's printed unit) and a ``sign`` of 1 or -1 (a debit balance printed
    as a positive debit).
    """
    out = [f"{w} = {v}" for w, v in _numbers({"case": case}) if v not in STRUCTURAL and w.split(".")[-1] != "periods"]
    for w, v in _numbers({"figures": specs}):
        last = w.split(".")[-1]
        if (last == "scale" and v in SCALES) or (last == "sign" and v in (1, -1)):
            continue
        if v not in STRUCTURAL and last != "at" and not re.fullmatch(r"periods\[\d+\]", last):
            out.append(f"{w} = {v}")
    return out
