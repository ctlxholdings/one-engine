"""The esco-product LOD 0 deals as an oracle family: published deal figures, run through the books.

The oracle
----------
Two vendored files, both unmodified from Ali's esco-product repository:

* ``cases/reference/esco/ontology/miniature/cases/<id>-lod0.yaml``: one deal
  (an energy performance contract, a street-lighting PPP, a solar
  securitisation...) at LOD 0. Its ``numbers`` block holds the figures the
  deal's sources print;
* ``cases/reference/esco/tests/test_lod0_numbers.py``: the answer key. Each
  ``check(id, kind, case, fn, published, decimals, citation, xfail)`` names a
  figure of one case, the value printed (``published``) and the decimals it
  is printed to. ``kind`` is ``SRC`` (a source prints the result), ``REC``
  (the case file's own arithmetic over printed inputs) or ``BAND`` (the
  printed figure sits within a stated band, ``decimals`` being the band).

The answer key is read by running it (it builds its list of checks at import;
several checks are generated in loops, so the list is not literal text). Its
own formulas (``fn``) are never used: the translation must reach each figure
through the engine.

The five sealed holdout deals (esco-product ``cases/sources/HOLDOUT_SEALED.md``)
have no case file; nothing here reads them.

Inputs and figures
------------------
* **inputs**: every number in the case's ``numbers`` block, named as
  :func:`one_engine.oracles.published.published_inputs` names them, except
  the ``derived`` and ``checks`` blocks (the transcriber's own arithmetic,
  which holds the answers) and ``engine_inputs`` (esco's own wiring). A text
  value counts for its first number, read with the answer key's own ``num``
  (``'220 000 (source; ...)'`` is 220000), because the answer key reads it
  that way, except the text under a key that describes a number
  (``source``, ``unit``, ``evidence``... ``DESCRIPTIONS``). The text's later
  numbers are inputs too, ``<name>__2``, ``<name>__3``... in order
  (``'HUF 11.1 billion (US$50 million)'`` gives ``annual_goal`` 11.1 and
  ``annual_goal__2`` 50), with source references (a file path, a code
  such as ``WB08:10543`` or ``AR18``, a section ``s.5.4``) cut out, a decimal comma read as one
  (``45,8``) and a dash between numbers read as a range, not a sign. A printed date or
  month is its parts, as the answer key's
  ``date`` and ``phase_bounds`` read it: ``contract_signed: 2006-06-01`` gives
  ``contract_signed__year``, ``__month`` and ``__day``; a range ``01/2010 to
  06/2016`` gives ``<name>__from__year`` ... ``<name>__to__month``.
* **figures**: the answer key's checks for this case, by check id. A check
  whose published value is not a number (a date, a word) and a check the
  answer key marks ``xfail`` (the source's figure does not re-compute) are set
  aside by the oracle itself, with its reason, and listed in the receipt.

Translation
-----------
``cases/esco/<id>.yaml``, the same keys as a published case
(:mod:`one_engine.oracles.published`) with ``esco: <id>`` and a formulas
generator taking ``inputs: esco``.

Checks
------
As for the published cases, with the answer key's own rule per figure:
``SRC`` and ``REC``, the engine's figure rounded to the printed decimals
equals the printed figure so rounded (``decimals`` None: equal); ``BAND``,
within ``decimals`` of it.

Planted error
-------------
The input named by ``plant``, times 1.01 (plus 1 when it is zero).
"""
from __future__ import annotations

import datetime as dt
import pathlib
import re
import runpy
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

import yaml

from one_engine.oracles import REFERENCE, Blocked, verify
from one_engine.oracles.published import PublishedFamily, _exact, _is_number, published_inputs

FOLDER = "esco"
CASES = f"{FOLDER}/ontology/miniature/cases"
ANSWER_KEY = f"{FOLDER}/tests/test_lod0_numbers.py"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "esco"
#: blocks of ``numbers`` that are not inputs: the transcriber's arithmetic (``derived``, and the
#: ``checks`` some deals keep beside it, which state results) and esco's engine wiring
NOT_INPUTS = ("derived", "checks", "engine_inputs")
#: a printed day (2006-06-01) or month (2006-07, 04/2007), as the answer key's ``date`` and
#: ``phase_bounds`` read them
_DAY = r"(\d{4})-(\d{2})-(\d{2})"
_MONTH = r"(\d{4})-(\d{2})|(\d{1,2})/(\d{4})"
_WHEN = re.compile(rf"\s*(?:{_DAY}|{_MONTH})")
_RANGE = re.compile(r"\s*(.+?)\s+to\s+(.+?)\s*$")
#: keys whose text describes a number (its citation, unit, evidence...) and is never one
DESCRIPTIONS = frozenset({"assumptions", "cite", "citation", "currency", "cusip", "evidence", "excluded_demoted",
                          "kind", "not_given", "note", "notes", "origin", "promise_kind", "quote", "read",
                          "scale_rule", "scale_status", "source", "unit"})


#: references inside a text that are not printed figures: a file path or source line
#: ('.../wb-public-procurement-2010.txt:2259-2260', 'WB08:10628-10640'), a code whose digits follow
#: its letters ('L2102-2104', 'AR18', 'Table CS12.1', 'CO2e', 'CVWD17'), a section or page ('s.5.4', 'p. 30')
_REFERENCE = re.compile(r"\S*[/\\]\S*|\S*\.(?:txt|pdf|md|ya?ml|html?|csv|xlsx?)\S*"
                        r"|\b[A-Za-z]+\d[\w.]*(?::\d+)?(?:[-–][A-Za-z]*\d+)?"
                        r"|\b(?:s|p|pp|para|art|sec)\.\s?\d[\d.\-–]*", re.IGNORECASE)
#: a number after the first: the answer key's pattern, plus a decimal comma ('45,8') that the answer
#: key's first-number reading does not know (it reads '15,5' as 15)
_LATER = re.compile(r"-?\d+,\d{1,2}(?![\d])|-?\d{1,3}(?:[ ,]\d{3})+(?:\.\d+)?(?!\d)|-?\d+(?:\.\d+)?")


@lru_cache(maxsize=None)
def answer_key() -> Dict[str, Any]:
    """The answer key's namespace (its ``CHECKS`` and its ``num``), after checking its hash."""
    verify(ANSWER_KEY)
    return runpy.run_path(str(REFERENCE / ANSWER_KEY), run_name="esco_answer_key")


def checks_for(case: str) -> List[Tuple[Any, ...]]:
    """The answer key's checks for one case: ``(id, kind, case, fn, published, decimals, cite, xfail)``."""
    return [c for c in answer_key()["CHECKS"] if c[2] == case]


@lru_cache(maxsize=None)
def _case(oid: str) -> Dict[str, Any]:
    return yaml.safe_load((REFERENCE / CASES / f"{oid}-lod0.yaml").read_text())


def _when(text: str) -> Optional[Dict[str, int]]:
    """A text that opens with a printed day or month, as ``{year, month[, day]}``; else None."""
    m = _WHEN.match(text)
    if not m:
        return None
    y, mo, d, y2, mo2, mo3, y3 = m.groups()
    if y:
        return {"year": int(y), "month": int(mo), "day": int(d)}
    return {"year": int(y2 or y3), "month": int(mo2 or mo3)}


def esco_inputs(case: Mapping[str, Any]) -> Dict[str, Any]:
    """The case's inputs by name (see the module docstring)."""
    num = answer_key()["num"]

    def strip(node: Any) -> Any:
        # a printed date or month range is its parts (a phase's months need the year, which the first
        # number alone would lose); a text that holds a number is that number, as the answer key reads
        # it; other text is dropped
        if isinstance(node, dt.date):
            return {"year": node.year, "month": node.month, "day": node.day}
        if isinstance(node, Mapping):
            out = {}
            for k, v in node.items():
                if k in DESCRIPTIONS and isinstance(v, str):
                    continue
                out[k] = strip(v)
                if isinstance(v, str) and _is_number(out[k]):
                    for i, later in enumerate(_later(v, out[k]), start=2):
                        out[f"{k}__{i}"] = later
            return out
        if isinstance(node, list):
            return [strip(v) for v in node]
        if isinstance(node, str):
            span = _RANGE.match(node)
            if span and _when(span.group(1)) and _when(span.group(2)):
                return {"from": _when(span.group(1)), "to": _when(span.group(2))}
            if _when(node):
                return _when(node)
            try:
                return num(node) if any(ch.isdigit() for ch in node) else node
            except (AttributeError, ValueError):
                return node
        return node

    def _later(text: str, first: Any) -> List[float]:
        # every number after the first, with references cut out. A minus is a sign only after a
        # space or bracket, not in a range ('2102-2104', '10-12m'). The first is the one ``num``
        # reads, so it is skipped when the cleaned text opens with it
        clean, found = _REFERENCE.sub(" ", text), []
        for m in _LATER.finditer(clean):
            g = m.group()
            if g.startswith("-") and m.start() > 0 and clean[m.start() - 1].isalnum():
                g = g[1:]
            g = g.replace(" ", "")
            found.append(float(g.replace(",", ".") if re.fullmatch(r"\d+,\d{1,2}", g) else g.replace(",", "")))
        return found[1:] if found and found[0] == float(first) else found

    numbers = {k: v for k, v in (case.get("numbers") or {}).items() if k not in NOT_INPUTS}
    return published_inputs({"inputs": strip(numbers)})


class EscoFamily(PublishedFamily):
    name = "esco"
    key = "esco"
    folder = CASES
    planted = "the input the translation names under 'plant', x 1.01 (+1 when it is zero)"
    tolerance = ("the answer key's rule per figure: SRC and REC, equal once rounded to the printed decimals; "
                 "BAND, within the stated band")

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        super().__init__(translations)

    def oracles(self) -> List[str]:
        return sorted(p.name[:-len("-lod0.yaml")] for p in (REFERENCE / CASES).glob("*-lod0.yaml"))

    def source(self, oid: str) -> str:
        return f"{CASES}/{oid}-lod0.yaml"

    def _load(self, oid: str) -> Tuple[Mapping[str, Any], Any]:
        if not checks_for(oid):
            raise Blocked("the answer key holds no figure for this case")
        return _case(oid), None

    def _inputs(self, oracle: Mapping[str, Any]) -> Dict[str, Any]:
        return esco_inputs(oracle)

    def _wanted(self, oid: str, oracle: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
        want: Dict[str, Any] = {}
        aside: Dict[str, str] = {}
        self._rules: Dict[str, Tuple[str, Optional[int]]] = {}
        for cid, kind, _, _, published, dp, _, xfail in checks_for(oid):
            want[cid] = _exact(published) if _is_number(published) else published
            self._rules[cid] = (kind, dp)
            if not _is_number(published):
                aside[cid] = f"the published figure is not a number ({published!r})"
            elif xfail:
                aside[cid] = f"the answer key marks it xfail: {xfail}"
        return want, {}, aside

    def _within(self, fig: str, value: Fraction, got: Fraction, tol: Any, canon: Optional[str]) -> Tuple[bool, str]:
        kind, dp = self._rules[fig]
        if kind == "BAND":
            return abs(got - value) <= _exact(dp), f"within the band {dp}"
        if dp is None:
            return got == value, "equal"
        # the answer key's own comparison: Python's round of the float, on both sides
        return round(float(got), dp) == round(float(value), dp), f"equal to {dp} decimals"

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """Claim the inventory rows of the LOD 0 cases (``ontology/miniature/cases/<id>-lod0.yaml``)."""
        path = row.get("path", "")
        if row.get("encoding_id") != "e_lod0_case" or not path.endswith("-lod0.yaml"):
            return None
        oid = path.rsplit("/", 1)[-1][:-len("-lod0.yaml")]
        return oid if (REFERENCE / CASES / f"{oid}-lod0.yaml").exists() else None
