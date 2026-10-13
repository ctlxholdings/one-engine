"""Two options of the income_tax rulebook the pilot golds needed: a minimum tax and progressive bands.

The minimum tax is the higher of the profits tax and a bounded turnover tax.

Nine pilot golds (the OHADA "impôt minimum forfaitaire" and its cousins)
charge the higher of the profits tax and a tax on turnover, floored and
capped; one of them computes it every period but waives it in the years a
holiday covers it. Before this option their translations recomputed the
profits tax in formulas and posted a top-up by transfer.
"""
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError

REVENUE = [0, 100, 1000]
COST = [50, 60, 100]


def _case(minimum=None):
    tax = {"kind": "income_tax", "entity": "shop", "phase": "tax", "expense": "tax_expense",
           "payable": "tax_payable", "rate": "0.3", "carry_forward_periods": None,
           "scheme": {"sales": "taxable", "costs": "taxable", "tax_expense": "non_deductible"}}
    if minimum is not None:
        tax["minimum"] = minimum
    return {
        "case": "minimum-tax", "periods": 3, "phases": ["operate", "tax"], "currency": "XOF", "floors": {},
        "generators": {}, "opening": {"shop": {"cash": {"nature": "asset", "value": 0},
                                               "equity": {"nature": "equity", "value": 0}}},
        "contracts": {},
        "rulebooks": {
            "trade": {"kind": "transfer", "entity": "shop", "phase": "operate",
                      "declare": {"sales": "income", "costs": "expense"},
                      "moves": {"sales": {"debit": "cash", "credit": "sales", "amount": REVENUE},
                                "costs": {"debit": "costs", "credit": "cash", "amount": COST}}},
            "tax": tax},
        "schemes": {}, "cuts": {}, "measures": {}}


def _tax(result):
    recs = result.records("tax")
    return [{k: recs[t][k] for k in ("profit_tax", "minimum", "tax")} for t in range(3)]


def test_the_charge_is_the_higher_of_the_profits_tax_and_the_bounded_turnover_tax():
    # turnover 0, 100, 1000 at 1%, floored at 5 and capped at 8: 5, 5, 8. The profits tax is nil while the
    # first year's loss of 50 is used (base -50, then 40), then 30% of 900 less the 10 left: 267
    r = run_case(_case({"turnover": ["sales"], "rate": "0.01", "floor": 5, "cap": 8}))
    assert _tax(r) == [{"profit_tax": 0, "minimum": 5, "tax": 5}, {"profit_tax": 0, "minimum": 5, "tax": 5},
                       {"profit_tax": 267, "minimum": 8, "tax": 267}]
    # the books carry what is charged
    assert r.group.book("shop").balance("tax_expense", 2) == 277
    # the minimum does not feed or draw on the loss pool
    assert [r.records("tax")[t]["losses"] for t in range(3)] == [50, 10, 0]


def test_without_a_minimum_the_charge_is_the_profits_tax():
    r = run_case(_case())
    assert [r.records("tax")[t]["tax"] for t in range(3)] == [0, 0, 267]


def test_a_waived_minimum_is_still_computed_but_not_charged():
    # a holiday that covers the minimum too: computed (the gold reports it), charged only where it applies
    r = run_case(_case({"turnover": ["sales"], "rate": "0.01", "floor": 5, "cap": 8, "applies": [0, 1, 1]}))
    assert _tax(r) == [{"profit_tax": 0, "minimum": 5, "tax": 0}, {"profit_tax": 0, "minimum": 5, "tax": 5},
                       {"profit_tax": 267, "minimum": 8, "tax": 267}]


def test_a_minimum_on_a_line_that_is_not_income_refuses():
    with pytest.raises(InputError, match="turnover"):
        run_case(_case({"turnover": ["costs"], "rate": "0.01"}))


# ── progressive bands ───────────────────────────────────────────────────────
# Three golds (paygo_solar, pharmacy_expiry, printer_cashbasis_v25) tax profit in bands. Before ``bands``
# their translations charged the top band's rate less the lower bands' relief, posted by transfer: right only
# while the base stays in the top band.

BANDS = [{"upto": 100, "rate": "0.1"}, {"upto": 500, "rate": "0.2"}, {"rate": "0.3"}]


def _banded(bands, **more):
    case = _case()
    tax = case["rulebooks"]["tax"]
    del tax["rate"]
    tax.update(bands=bands, **more)
    return case


def test_each_band_taxes_the_base_inside_it():
    # bases -50, 40, 900; the loss of 50 is used first, so the taxable amounts are 0, 0, 890:
    # 100 at 10%, 400 at 20%, 390 at 30% = 10 + 80 + 117 = 207
    r = run_case(_banded(BANDS))
    recs = r.records("tax")
    assert [recs[t]["tax"] for t in range(3)] == [0, 0, 207]
    assert [recs[2][f"band{i}_base"] for i in (1, 2, 3)] == [100, 400, 390]
    assert [recs[2][f"band{i}_tax"] for i in (1, 2, 3)] == [10, 80, 117]


def test_bands_set_on_an_annual_base_tax_each_period_at_its_annual_equivalent():
    # a monthly grid taxed on annual bands: each month's taxable amount times 12, banded, divided by 12
    # (890 x 12 = 10,680: 10 + 80 + 0.3 x 10,180 = 3,144, a month 262)
    r = run_case(_banded(BANDS, bands_per_year=12))
    assert r.records("tax")[2]["tax"] == 262
    assert r.records("tax")[2]["band3_base"] == F(10180, 12)


def test_bands_refuse_a_rate_beside_them_and_bands_out_of_order():
    case = _banded(BANDS)
    case["rulebooks"]["tax"]["rate"] = "0.3"
    with pytest.raises(InputError, match="rate or bands"):
        run_case(case)
    with pytest.raises(InputError, match="bands"):
        run_case(_banded([{"upto": 500, "rate": "0.1"}, {"upto": 100, "rate": "0.2"}, {"rate": "0.3"}]))
