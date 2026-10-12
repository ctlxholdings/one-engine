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
  the ``derived`` block (the transcriber's own arithmetic, which holds the
  answers) and ``engine_inputs`` (esco's own wiring). A text value counts
  for its first number, read with the answer key's own ``num`` (``'220 000
  (source; ...)'`` is 220000), because the answer key reads it that way.
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

import pathlib
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
#: blocks of ``numbers`` that are not inputs: the transcriber's arithmetic and esco's engine wiring
NOT_INPUTS = ("derived", "engine_inputs")


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


def esco_inputs(case: Mapping[str, Any]) -> Dict[str, Any]:
    """The case's inputs by name (see the module docstring)."""
    num = answer_key()["num"]

    def strip(node: Any) -> Any:
        # a text that holds a number is that number, as the answer key reads it; other text is dropped
        if isinstance(node, Mapping):
            return {k: strip(v) for k, v in node.items()}
        if isinstance(node, list):
            return [strip(v) for v in node]
        if isinstance(node, str):
            try:
                return num(node) if any(ch.isdigit() for ch in node) else node
            except (AttributeError, ValueError):
                return node
        return node

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
