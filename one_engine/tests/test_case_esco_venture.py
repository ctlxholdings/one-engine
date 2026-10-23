"""Reproduction test: esco-product's ESCO venture pipeline (Capelo, Ferreira Dias and Pereira, 2017) run on one_engine matches esco-product bit for bit.

Where the numbers come from
---------------------------
esco-product rebuilt the published system-dynamics model of a new ESCO from
its documentation's equations and admitted constants
(``engine/oe/firm/pipeline.py``, constants in
``ontology/miniature/firm/capelo_pipeline_constants.yaml``) and runs it four
ways: its base case (the two policy switches read as off) and the appendix
read literally, each at a monthly and a quarter-month Euler step.
``cases/reference/esco_engine/capelo_pipeline.json`` (hash-pinned) holds the
constants and printed units it reads, each run's year-end rows and summary,
its unit check and its market value added with the cost of capital read as an
annual effective rate.

What is compared
----------------
The model is floating point and esco-product's order of operations is kept,
so every year-end row (staff, prospects, adopters, projects in progress,
accumulated net earnings, MVA, cash, debt, equity, revenues, net earnings,
learning factor, potential market), every summary figure, the unit check and
the MVA variant are compared for equality, not within a tolerance. The money
side is posted to one_engine's book: it must balance exactly every year (cash
plus capital in projects equals debt plus equity with the year's earnings)
and follow the model's own levels to 1e-9 (the drift between summing exact
postings and Euler-updating floats).

The debt-cost curve is the printed graph esco-product reads (D:400): 6.5% up
to a debt ratio of 0.5, then 7, 8.5, 10, 12, 14, 16, 18, 19.5, 20 and 20%,
at steps of 0.05.

If this fails, the engine or the translation is wrong, never the number.
"""
import copy
import json
import pathlib

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError
from one_engine.oracles import verify
from one_engine.packs import venture as V

REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"
REL = "esco_engine/capelo_pipeline.json"


def load():
    verify(REL)
    return json.loads((REFERENCE / REL).read_text())


REF = load()
CONSTANTS = REF["constants"]["constants"]
UNITS = REF["constants"]["printed_units"]
CURVE = {"x0": 0, "step": 0.05, "ys": [0.065] * 11 + [0.07, 0.085, 0.10, 0.12, 0.14, 0.16, 0.18, 0.195, 0.2, 0.2]}
SWITCHES = {"base case (by our rule)": {"audit_multiplier_1": True, "market_cost_of_debt": True},
            "appendix read literally": {"audit_multiplier_1": False, "market_cost_of_debt": False}}
STEPS = {"monthly step": "1/12", "quarter-month step": "1/48"}
BASE = "base case (by our rule)|monthly step"


def case(switches, step, constants=CONSTANTS, units=UNITS):
    rb = {"kind": "venture", "entity": "esco", "phase": "settle", "constants": constants, "switches": switches,
          "debt_cost_curve": CURVE, "step": step}
    if units is not None:
        rb["printed_units"] = units
    return {"case": "venture", "periods": 15, "phases": ["settle"], "currency": "KEUR", "floors": {}, "generators": {},
            "contracts": {}, "schemes": {}, "cuts": {}, "measures": {}, "rulebooks": {"venture": rb}}


@pytest.mark.parametrize("key", sorted(REF["runs"]))
def test_run(key):
    reading, label = key.split("|")
    exp = REF["runs"][key]
    res = run_case(case(SWITCHES[reading], STEPS[label], units=UNITS if key == BASE else None))
    assert res.failed_checks() == []
    recs = res.records("venture")
    last = recs[14]
    assert last["rows"] == exp["rows"]
    assert last["summary"] == exp["summary"]
    for t in range(15):
        lv, row = recs[t]["levels"], recs[t]["row"]
        assert row == exp["rows"][t + 1]
        assert lv["cash"] + lv["capital_in_projects"] == lv["debt"] + lv["equity_with_earnings"]
        for acct, col in (("cash", "cash"), ("debt", "debt"), ("equity_with_earnings", "equity")):
            assert abs(float(lv[acct]) - row[col]) < 1e-9, (t, acct)
    if key == BASE:
        assert last["mva_annual_effective"] == REF["mva_annual_effective"]
        inside, rows = last["unit_check"]
        assert [list(x) for x in inside] == REF["unit_check"]["inside"]
        assert rows == REF["unit_check"]["rows"]


def test_base_case_against_the_paper_shape():
    s = REF["runs"][BASE]["summary"]
    assert s["first projects year"] == 2 and s["negative years of accumulated net earnings"] == 6


def test_refuses_an_initial_cash_other_than_debt_plus_equity():
    rows = copy.deepcopy(CONSTANTS)
    next(x for x in rows if x["name"] == "Initial Cash")["value"] = "500"
    with pytest.raises(InputError, match="Initial Cash"):
        V.constants(rows)


def test_refuses_an_unknown_unit_and_bad_switches():
    with pytest.raises(InputError, match="unknown unit"):
        V.parse_unit("K Eur/fortnight")
    with pytest.raises(InputError, match="switches"):
        run_case(case({"audit_multiplier_1": True}, "1/12"))


def test_unit_check_traces_a_dimension_mismatch():
    rows = copy.deepcopy(CONSTANTS)
    next(x for x in rows if x["name"] == "Overhead Costs")["unit"] = "K Eur"     # a stock where a flow is added
    inside, _ = V.unit_check(rows, UNITS, SWITCHES["base case (by our rule)"], CURVE)
    assert ("Operating Costs", "+", "KEur/T", "KEur") in inside


def test_negative_taxes_post_the_other_way():
    res = run_case(case(SWITCHES["appendix read literally"], "1/12", units=None))
    recs = res.records("venture")
    assert recs[0]["row"]["net earnings"] < 0 and recs[0]["levels"]["taxes"] < 0
