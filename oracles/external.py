"""The golds library's external fixtures as an oracle family: outside models with their own answers.

The oracle
----------
``cases/reference/external/<id>.json``, vendored unmodified from Ali's golds
library (``one-place/cases/external/fixtures``). Each holds the figures one
outside source published, extracted (the source workbook is not
redistributed):

* ``cfi_3statement``: Corporate Finance Institute's three-statement model, six
  years of balance sheet lines;
* ``modeloff_property``: ModelOff "Buying an Investment Property", official
  answers, the apartment at fair value, the loan and the equity;
* ``ias36_impairment``: an IAS 36 cash-generating-unit impairment, value in
  use as the recoverable amount;
* ``ias29_hyperinflation``: an IAS 29 net monetary position worked example;
* ``open_securitisation``: openSecuritisation's (Open Risk, Apache-2.0)
  zero-default 20-period waterfall with four ranked bonds and coverage tests.

``engine_pinned.json``, in the same folder at the origin, is not vendored: it
pins the golds engine's own output for two of its own scenarios, which is not
an outside answer.

The library's own test file, ``test_external_cases.py``, is vendored beside
the fixtures because it states the comparison rule (below). Its cases are not
run here: several feed answers back in as inputs (CFI's receivables,
inventory and payables balances drive its collections and purchases;
openSecuritisation's bond payments drive its notionals), so those lines pass
by construction there. Here an answer is never an input.

Inputs and figures
------------------
A fixture has no common shape, so it is read mechanically, the way
:mod:`one_engine.oracles.published` reads a published case:

* **inputs**: every number outside the answers (``assertions``,
  ``expected_outputs``), the fixture's ``tolerance`` and its ``_source``,
  named as :func:`~one_engine.oracles.published.published_inputs` names them:
  ``revenue`` (a series), ``openings.ppe`` as ``openings__ppe`` where a
  shorter tail is shared, ``structure.bonds[0].bond_spread`` as
  ``bonds_0__bond_spread``;
* **figures**: every number under ``assertions`` or ``expected_outputs``, by
  dotted path (``bond_notionals.A1``, ``_sanity.total_senior_fees``); a list
  of numbers is one series figure.

Every figure is an answer the source published, so every figure is scored:
none may be set aside. A figure the inputs cannot derive is left unread and
reports "not reported", with the reason in the translation's header.

Comparison rule
---------------
The library test's own tolerances (``test_external_cases.py``), per figure:

* ``CENT`` (read from the test file) on every figure by default: the
  three-statement, property and hyperinflation lines are held to it;
* the fixture's own ``tolerance`` on the IAS 36 lines allocated pro rata
  (``property``, ``plant``, ``patent``): the published allocation is rounded
  to the nearest thousand (185 / 123 / 92 thousand against an exact 184.62 /
  123.08 / 92.31); goodwill, the CGU total and the loss are held to CENT;
* ``1e-9`` on every openSecuritisation figure: the test's tolerance on the
  bond notionals, which also covers the coverage ratios printed to ten
  decimals (a rounding of at most 5e-11).

Translation
-----------
``cases/external/<id>.yaml``, the keys of a published translation with
``external: <id>`` and a formulas generator taking ``inputs: external``.

Planted error
-------------
The input named by ``plant``, times 1.01 (plus 1 when it is zero).
"""
from __future__ import annotations

import ast
import json
import pathlib
import re
from fractions import Fraction
from functools import lru_cache
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine.oracles import REFERENCE, verify
from one_engine.oracles.golds import _fmt
from one_engine.oracles.published import PublishedFamily, _exact, _leaves, published_inputs

FOLDER = "external"
TESTS = f"{FOLDER}/test_external_cases.py"
TRANSLATIONS = pathlib.Path(__file__).resolve().parent.parent / "cases" / "external"
#: the blocks of a fixture that hold its answers
ANSWERS = ("assertions", "expected_outputs")
#: blocks that are neither input nor answer: the comparison rule and the citation
NOT_INPUTS = ANSWERS + ("tolerance", "_source")
#: the IAS 36 lines the library test holds to the fixture's ``tolerance`` (the pro rata allocation, printed
#: to the nearest thousand); every other line of that fixture is held to CENT
ROUNDED_LINES = {"ias36_impairment": ("property", "plant", "patent")}
#: the library test's tolerance on openSecuritisation's notionals, applied to every figure of that fixture
SECURITISATION = ("open_securitisation", Fraction("1e-9"))


@lru_cache(maxsize=None)
def _fixture(oid: str) -> Dict[str, Any]:
    return json.loads((REFERENCE / FOLDER / f"{oid}.json").read_text())


@lru_cache(maxsize=None)
def cent() -> Fraction:
    """The library test's ``CENT``, read from the vendored test file (after checking its hash)."""
    verify(TESTS)
    tree = ast.parse((REFERENCE / TESTS).read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id == "CENT" and isinstance(node.value, ast.Constant)):
            return _exact(node.value.value)
    raise ValueError(f"{TESTS}: no CENT")


def external_inputs(fixture: Mapping[str, Any]) -> Dict[str, Any]:
    """The fixture's inputs by name (see the module docstring)."""
    return published_inputs({"inputs": {k: v for k, v in fixture.items() if k not in NOT_INPUTS}})


def external_figures(fixture: Mapping[str, Any]) -> Dict[str, Any]:
    """Every answer the fixture publishes, by dotted path: a number, or a series."""
    out: Dict[str, Any] = {}
    for block in ANSWERS:
        for path, v in _leaves(fixture.get(block) or {}, (), text=False):
            out[".".join(str(k) for k in path)] = v
    return out


class ExternalFamily(PublishedFamily):
    """The external fixtures (see the module docstring)."""

    name = "external"
    key = "external"
    folder = FOLDER
    planted = "the input the translation names under 'plant', x 1.01 (+1 when it is zero)"
    tolerance = ("the library test's own: CENT on every line, the fixture's tolerance on the IAS 36 pro rata "
                 "lines, 1e-9 on openSecuritisation")

    def __init__(self, translations: pathlib.Path = TRANSLATIONS):
        super().__init__(translations)

    def oracles(self) -> List[str]:
        return sorted(p.stem for p in (REFERENCE / FOLDER).glob("*.json"))

    def source(self, oid: str) -> str:
        return f"{FOLDER}/{oid}.json"

    def _load(self, oid: str) -> Tuple[Mapping[str, Any], Any]:
        fixture = _fixture(oid)
        wider = {f: _exact(fixture["tolerance"]) for f in ROUNDED_LINES.get(oid, ())}
        default = SECURITISATION[1] if oid == SECURITISATION[0] else cent()
        return fixture, {"default": default, "figures": wider}

    def _inputs(self, oracle: Mapping[str, Any]) -> Dict[str, Any]:
        return external_inputs(oracle)

    def _wanted(self, oid: str, oracle: Mapping[str, Any]) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, str]]:
        want = external_figures(oracle)
        # every figure is a published answer, so every one is scored and none may be set aside
        return want, {f: None for f in want}, {}

    def _within(self, fig: str, value: Fraction, got: Fraction, tol: Any, canon: Optional[str]) -> Tuple[bool, str]:
        allowed = tol["figures"].get(fig, tol["default"])
        # _fmt prints four decimals, which would show openSecuritisation's 1e-9 as 0
        shown = _fmt(allowed) if allowed >= Fraction(1, 10000) else f"{float(allowed):g}"
        return abs(got - value) <= allowed, f"within {shown}"

    def inventory_oid(self, row: Mapping[str, str]) -> Optional[str]:
        """Claim the inventory rows of the external fixtures (``cases/external/fixtures/<id>.json``)."""
        if row.get("encoding_id") != "g_external_fixture_json":
            return None
        m = re.search(r"cases/external/fixtures/([A-Za-z0-9_]+)\.json$", row.get("path", ""))
        if m and (REFERENCE / FOLDER / f"{m.group(1)}.json").exists():
            return m.group(1)
        return None
