"""The asset register's gross view of fixed assets carried in on an opening balance sheet.

Two pilot golds (isp_levy, cattle_embouche) open with assets already part
depreciated and report their cost and accumulated depreciation apart. The
books carry such an asset at its net book value, so the register's gross view
(``cost``, ``accumulated``) started from that value and the depreciation
charged before the first period showed nowhere; the translations added it back
as a driver. ``carried_accumulated`` states it: the gross view's cost and
accumulated depreciation both start higher by it; the books and the charge
are unchanged.
"""
import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError


def _case(**register):
    reg = {"kind": "asset_register", "entity": "shop", "phase": "close", "from": "cwip", "to": "fixed_assets",
           "expense": "depreciation", "disposal": "retirement_loss", "in_service": 0, "dispose_at": 2,
           "life_periods": 3, "first_charge": "in_service", **register}
    return {
        "case": "carried-assets", "periods": 3, "phases": ["close"], "currency": "XOF", "floors": {},
        "generators": {},
        "opening": {"shop": {"fixed_assets": {"nature": "asset", "value": 600},
                             "cwip": {"nature": "asset", "value": 0},
                             "equity": {"nature": "equity", "value": 600}}},
        "contracts": {}, "rulebooks": {"assets": reg}, "schemes": {}, "cuts": {}, "measures": {}}


def test_the_depreciation_charged_before_the_first_period_is_in_the_gross_view():
    # carried at 600 net of 400 already charged: cost 1,000; 200 a period over the 3 left
    r = run_case(_case(carried_accumulated=400))
    recs = r.records("assets")
    assert [recs[t]["cost"] for t in range(2)] == [1000, 1000]
    assert [recs[t]["accumulated"] for t in range(2)] == [600, 800]
    assert [recs[t]["charge"] for t in range(3)] == [200, 200, 200]
    # retired at the end of period 2: the whole cost and every charge, the pre-opening ones included
    assert (recs[2]["disposed_cost"], recs[2]["disposed_accumulated"], recs[2]["disposed_nbv"]) == (1000, 1000, 0)
    # the books are as before: the asset at its net book value, run down to nil
    book = r.group.book("shop")
    assert [book.balance("fixed_assets", t) for t in range(3)] == [400, 200, 0]


def test_without_it_the_gross_view_starts_from_the_net_book_value():
    recs = run_case(_case()).records("assets")
    assert (recs[0]["cost"], recs[0]["accumulated"]) == (600, 200)


def test_a_carried_accumulation_with_nothing_carried_in_refuses():
    case = _case(carried_accumulated=400)
    case["opening"]["shop"]["fixed_assets"]["value"] = 0
    case["opening"]["shop"]["equity"]["value"] = 0
    with pytest.raises(InputError, match="nothing is carried in"):
        run_case(case)
