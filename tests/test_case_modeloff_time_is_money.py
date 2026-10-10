"""ModelOff 2014 "Time is Money": a US$ business valued for a euro buyer (plan step 7p).

Referee: the competition's seven answers, with the forecast, the indices,
the exchange rates and the depreciation and tax terms, as transcribed from
its data workbook in
``generic_engine/tests/test_benchmark_round1_time_is_money.py`` and copied
to ``cases/reference/modeloff_2014_time_is_money.json`` by
``consolidation/experiments/make_reference_modeloff_time_is_money.py`` (data
read from that module's constants; methods and answers checked against its
text). The workbook is not in the repository, so the transcription is not
re-checked against it here. The answers are stated to the rounding the
paper gives; one_engine must round to exactly them. Q1 (an exchange rate)
checks the inputs layer only.

The books are kept in nominal US$, the business's own currency. The paper's
euro figures are each year's US$ lines at that year's nominal rate, a view
computed here (``eur``); the NPVs are converted at the spot rate.

What the case adds to the engine (convergence count 2): Measures gains
``xnpv`` (dated flows, actual/365, as Excel's XNPV), and the case runner
wires the ``npv`` and ``xnpv`` measures with a rate and a last period
valued (``through``). Pack changes (not core): a ``capex_class`` contract
holds an asset class's spend in its own account, and the asset register's
event ids carry the rulebook's name, so two registers can share a book.
Reused unchanged: the offtake, operating cost and sponsor equity
contracts, the opening balance sheet, the asset register (declining
balance from step 7i, straight line, from the period after purchase),
income tax, the waterfall's tax tier and the cash flow cut.
"""
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine import measures as M
from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_time_is_money.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2014_time_is_money.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = CASE["periods"]


def index(key):
    """A cumulative inflation index by year: 1 in year 0."""
    out = [F(1)]
    for x in I[key]:
        out.append(out[-1] * (1 + F(str(x))))
    return out


USD, EUR = index("usd_inflation"), index("eur_inflation")
FX = [F(str(x)) * USD[t] / EUR[t] for t, x in enumerate(I["real_fx_usd_per_eur"])]     # US$ per euro, nominal
SPOT = F(str(I["spot_fx"]))


def nominal(key):
    return [F(0)] + [F(str(x)) * USD[t + 1] for t, x in enumerate(I[key])]


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def lines(book, t):
    """Year ``t``'s profit and loss lines in US$: revenue, costs, depreciation, profit before tax, tax."""
    rev, cost = mv(book, "sales.revenue", t), mv(book, "costs.expense", t)
    dep = mv(book, "class_a_depreciation", t) + mv(book, "class_b_depreciation", t)
    return {"revenue": rev, "costs": cost, "depreciation": dep, "npbt": rev - cost - dep,
            "tax": mv(book, "tax_expense", t)}


def eur(x, t):
    """A US$ amount of year ``t`` in euro, at that year's nominal rate."""
    return x / FX[t]


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("division")


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_inflates_the_forecast_and_copies_the_terms():
    c = CASE["contracts"]
    assert [F(x) for x in c["sales"]["terms"]["price"]] == nominal("revenue_real")
    assert [F(x) for x in c["costs"]["terms"]["volume"]] == nominal("opex_real")
    assert [F(x) for x in c["class_a"]["terms"]["schedule"]] == nominal("capex_class_a_real")
    assert [F(x) for x in c["class_b"]["terms"]["schedule"]] == nominal("capex_class_b_real")
    rb = CASE["rulebooks"]
    assert F(rb["class_a_register"]["declining_rate"]) == F(I["class_a_rate"])
    assert rb["class_b_register"]["life_periods"] == I["class_b_life"]
    assert {rb[k]["first_charge"] for k in ("class_a_register", "class_b_register")} == {"next_period"}
    assert F(str(rb["tax"]["rate"])) == F(str(I["tax_rate"]))
    o = CASE["opening"]["division"]
    assert (o["class_a_assets"]["value"], o["class_b_assets"]["value"]) == (I["opening_class_a"], I["opening_class_b"])
    m = CASE["measures"]
    assert m["npv_10"]["through"] == m["xnpv_10"]["through"] == 10
    assert m["xnpv_10"]["dates"][0] == dt.date.fromisoformat(I["valuation_date"])


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_tax_is_paid_the_year_it_is_charged(res, book):
    assert res.failed_checks() == []
    assert all(book.balance("tax_payable", t) == 0 for t in range(N))


def test_q1_the_nominal_exchange_rate_in_year_7():
    # the inputs layer only: the real rate carried by the two inflation indices
    assert round(float(FX[7]), 3) == A["q1_nominal_fx_year_7"]


def test_q2_revenue_in_year_2_in_euro(book):
    assert round(float(eur(lines(book, 2)["revenue"], 2))) == A["q2_revenue_year_2_eur"]


def test_q3_depreciation_in_year_6_in_euro(book):
    assert round(float(eur(lines(book, 6)["depreciation"], 6)), 1) == A["q3_depreciation_year_6_eur"]


def test_q4_profit_before_tax_in_year_4_in_euro(book):
    assert round(float(eur(lines(book, 4)["npbt"], 4)), 1) == A["q4_npbt_year_4_eur"]


def test_q5_tax_in_year_8_in_euro(book):
    assert round(float(eur(lines(book, 8)["tax"], 8))) == A["q5_tax_year_8_eur"]


def test_q6_the_npv_of_the_first_ten_years_in_euro(res):
    flows = res.cut("free_cash_flow").values
    assert res.measures["npv_10"] == pytest.approx(sum(float(flows[t]) / 1.1 ** t for t in range(11)), rel=1e-12)
    assert round(res.measures["npv_10"] / float(SPOT)) == A["q6_npv_10_years_eur"]


def test_q7_the_xnpv_is_lower_by_about_181000_euro(res):
    less = (res.measures["npv_10"] - res.measures["xnpv_10"]) / float(SPOT) * 1e6
    closest = min(A["q7_choices"], key=lambda c: abs(c - less))
    assert closest == A["q7_npv_less_xnpv_eur_closest_thousand"]


# ── how the books are built ───────────────────────────────────────────────

def test_class_a_depreciates_at_40_percent_of_its_balance_from_the_year_after_purchase(book):
    capex = nominal("capex_class_a_real")
    nbv = [F(I["opening_class_a"])] + [F(0)] * (N - 1)
    for t in range(1, 4):
        # each year: 40% of the balance carried in, then that year's capex joins it undepreciated
        assert mv(book, "class_a_depreciation", t) == F(4, 10) * nbv[t - 1]
        nbv[t] = nbv[t - 1] * F(6, 10) + capex[t]
        assert book.balance("class_a_assets", t) == nbv[t]


def test_class_b_depreciates_straight_line_over_four_years(book):
    capex = nominal("capex_class_b_real")
    for t in range(1, 8):
        bought = [F(I["opening_class_b"])] + capex[1:]
        assert mv(book, "class_b_depreciation", t) == sum(bought[k] / 4 for k in range(max(0, t - 4), t))


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_euro_tax_is_the_dollar_tax_translated(book):
    # every line of a year is translated at the same rate, so the tax on the euro profit is the
    # euro value of the tax on the dollar profit: the books need not be kept in euro
    for t in range(1, N - 1):
        x = lines(book, t)
        assert x["npbt"] > 0 and F(4, 10) * eur(x["npbt"], t) == eur(x["tax"], t)


def test_finding_class_a_has_no_cut_off(book):
    # the 40% rate comes from a 5-year life, but the balance is never written down to nil
    assert all(mv(book, "class_a_depreciation", t) > 0 for t in range(1, N - 1))
    assert book.opening("class_a_assets", N - 1) > 0


def test_finding_the_npv_and_the_xnpv_differ_by_the_leap_days_alone(res):
    flows = res.cut("free_cash_flow").values[:11]
    start = dt.date(2014, 12, 31)
    even = [start + dt.timedelta(days=365 * t) for t in range(11)]
    assert M.xnpv(flows, even, 0.1) == pytest.approx(res.measures["npv_10"], rel=1e-12)


# ── refusals ──────────────────────────────────────────────────────────────

def test_an_npv_through_a_period_past_the_cut_refuses():
    def far(case):
        case["measures"]["npv_10"]["through"] = N
    with pytest.raises(InputError, match="through period"):
        variant(far)


def test_an_xnpv_with_a_date_missing_refuses():
    with pytest.raises(M.MeasureError, match="dates"):
        M.xnpv([1, 2, 3], [dt.date(2014, 12, 31), dt.date(2015, 12, 31)], 0.1)


def test_an_npv_with_no_rate_refuses():
    def no_rate(case):
        del case["measures"]["npv_10"]["rate"]
    with pytest.raises(InputError, match="rate"):
        variant(no_rate)
