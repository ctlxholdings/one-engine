"""ModelOff 2015 "More Money Please": a project-finance refinancing, parts A and B (plan step 7h).

Referee: the competition's ten verified multiple-choice answers, with the
inputs, as transcribed from its data workbook and answer key in
``generic_engine/tests/test_benchmark_round2_more_money_please.py`` and
copied to ``cases/reference/modeloff_2015_more_money_please.json`` by
``consolidation/experiments/make_reference_modeloff_mmp.py``. The
workbook itself is not in the repository, so the transcription is not
re-checked against it here; that ten independent answers come out of the
transcribed inputs is the evidence that both are right. The answers are
rounded as the competition states them (to the dollar, four decimals, or
the nearest thousand); one_engine must round to exactly them.

Part A refinances the senior debt at the end of 2016 with a loan sized to
repay it net of a 2% fee and repaid straight-line by days; part B sculpts
that loan to a 1.80x DSCR instead (the test builds part B from the case
file by swapping its sizing loop).

What the case adds to the engine (convergence count 1): ``Measurement``
gains weighted straight-line depreciation (``Policy.weights``: a tranche
spread over the weights left, ModelOff's days of operation left). Pack
changes (not core): the asset register takes weights and depreciates fixed
assets carried on an opening balance sheet; the waterfall's CFADS takes a
``share`` by period (the days the debt is outstanding); a ``refinance``
loop rule sizes a loan to repay another net of fees. Reused unchanged:
the offtake, operating cost and capex contracts, the loan twice, the
corporate pack's financing fee, sponsor equity, the opening balance sheet,
loops and the ``sculpt`` rule.
"""
import calendar
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict
from one_engine.packs.pf import SizingError

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_more_money_please.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2015_more_money_please.json").read_text())
I, A, INTEGRITY = REF["inputs"], REF["answers"], REF["integrity"]
YEARS = list(range(I["first_year"], I["last_year"] + 1))
N = len(YEARS)
Y = YEARS.index


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


def part_b(case):
    """Part B: the refinancing sculpted to the target DSCR in every year."""
    case["loops"]["refi_size"]["rule"] = {"kind": "sculpt", "loan": "refi", "dscr": I["refinancing"]["target_dscr"],
                                          "cfads": "waterfall"}


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def resb():
    return variant(part_b)


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def _overlap(y, start, end):
    a, b = max(dt.date(y, 1, 1), start), min(dt.date(y, 12, 31), end)
    return max(0, (b - a).days + 1)


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_inputs_and_derives_the_calendar():
    c, rb = CASE["contracts"], CASE["rulebooks"]
    in_year = [366 if calendar.isleap(y) else 365 for y in YEARS]
    operating = [_overlap(y, dt.date(YEARS[0], 1, 1), dt.date.fromisoformat(I["project_end"])) for y in YEARS]
    ref = I["refinancing"]
    outstanding = [_overlap(y, dt.date.fromisoformat(ref["first_day"]), dt.date.fromisoformat(ref["maturity"]))
                   for y in YEARS]
    index = [(1 + F(str(I["inflation"]))) ** k for k in range(N)]
    share = [F(o, n) for o, n in zip(operating, in_year)]
    assert [F(x) for x in c["sales"]["terms"]["volume"]] == share
    assert [F(x) for x in c["sales"]["terms"]["price"]] == [I["revenue_real_pa"] * x for x in index]
    assert c["opex"]["terms"]["annual"] == I["opex_real_pa"]
    assert [F(x) for x in c["opex"]["terms"]["index"]] == [x * s for x, s in zip(index, share)]
    assert [F(x) for x in c["lifecycle"]["terms"]["schedule"]] == [a * x for a, x in zip(I["lifecycle_real"], index)]
    assert [int(x) for x in rb["assets"]["weights"]] == operating
    assert c["refi"]["terms"]["days_per_period"] == outstanding == c["refi_fee"]["terms"]["year_fraction"]
    assert c["refi_fee"]["terms"]["years"] == sum(outstanding) == 7121
    cover = [F(x) for x in rb["waterfall"]["cfads"]["share"]]
    assert cover[:2] == [1, 1] and cover[Y(2036)] == F(182, 366) and cover[Y(2017)] == 1 and cover[Y(2037)] == 0
    assert F(str(c["refi"]["terms"]["rate"])) == F(str(ref["rate"]))
    assert c["refi"]["terms"]["day_count_basis"] == ref["day_count_basis"]
    assert F(str(c["refi_fee"]["terms"]["rate"])) == F(str(ref["arrangement_fee"]))
    s, bs = c["senior"]["terms"], I["balance_sheet_2014"]
    assert F(str(s["rate"])) == F(str(I["senior"]["rate"])) and s["repay_periods"] == I["senior"]["repayment_years"]
    assert s["commitment"] / s["repay_periods"] == I["senior"]["repayment"]
    opening = {k: v["value"] for k, v in CASE["opening"]["company"].items()}
    assert opening == {"fixed_assets": bs["fixed_assets"], "cash": bs["cash"], "senior.debt": bs["senior_debt"],
                       "retained_earnings": "balancing"}
    assert c["refi"]["terms"]["repay_from"] == Y(2017) and Y(2017) + c["refi"]["terms"]["repay_periods"] - 1 == Y(2036)


def test_the_inputs_reproduce_the_spreadsheets_own_figures(res):
    book = res.group.book("company")
    cf = res.cut("project").values
    for y in (2015, 2016, 2038):
        assert round(cf[Y(y)]) == INTEGRITY[f"project_cf_{y}"]
    assert mv(book, "senior.interest", Y(2015)) == INTEGRITY["senior_interest_2015"]
    assert mv(book, "senior.interest", Y(2016)) == INTEGRITY["senior_interest_2016"]
    assert round(book.balance("fixed_assets", Y(2020))) == INTEGRITY["fixed_assets_2020"]
    assert float(F(str(CASE["contracts"]["sales"]["terms"]["price"][Y(2020)])) / 5_000_000) == \
        pytest.approx(INTEGRITY["index_2020"], abs=1e-10)


# ── part A ────────────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_the_loop_closes_on_its_second_run(res):
    assert res.failed_checks() == []
    assert res.loops["refi_size"].iterations == 2 and res.loops["refi_size"].residual == 0
    assert res.balancing["company"] == ("retained_earnings", 1_000_000)


def test_q1_the_amount_drawn_repays_the_senior_net_of_the_fee(res):
    drawn = res.loops["refi_size"].values["debt"]
    assert round(drawn) == A["q1_amount_drawn"]
    assert drawn == pytest.approx(9_000_000 / 0.98, abs=1e-6)
    book = res.group.book("company")
    assert book.balance("senior.debt", Y(2016)) == 0


def test_q2_q3_q4_the_refinancing_schedules(res):
    book = res.group.book("company")
    paid = res.records("waterfall")[Y(2022)]["paid"]
    assert round(paid["refi.principal"]) == A["q2_repayment_2022"]
    assert round(mv(book, "refi_fee.amortisation", Y(2024))) == A["q3_fee_amortisation_2024"]
    assert round(sum(mv(book, "refi.interest", t) for t in range(N))) == A["q4_total_interest"]
    # repaid and amortised to nothing at the half-year maturity
    assert book.balance("refi.debt", Y(2036)) == 0 and book.balance("refi_fee.deferred", Y(2036)) == 0


def test_q5_q6_the_dividend_and_the_net_assets(res):
    book = res.group.book("company")
    div = res.cut("dividends").values
    assert round(-div[Y(2034)]) == A["q5_dividend_2034"]
    t = Y(2020)
    net = (book.balance("fixed_assets", t) + book.balance("refi_fee.deferred", t) + book.balance("cash", t)
           - book.balance("refi.debt", t) - book.balance("senior.debt", t))
    assert round(net) == A["q6_net_assets_2020"]


def test_q7_the_average_dscr_over_the_refinancing(res):
    w = res.records("waterfall")
    dscr = [w[t]["dscr"] for t in range(Y(2017), Y(2036) + 1)]
    assert round(float(sum(dscr) / len(dscr)), 4) == A["q7_average_dscr"]


def test_every_dollar_is_paid_out_and_the_books_wind_down(res):
    book = res.group.book("company")
    assert all(book.balance("cash", t) == 0 for t in range(N))
    # 2015's dividend includes the opening cash: 1,000,000 + 2,000,000 - 1,200,000 of senior debt service
    assert -res.cut("dividends").values[0] == 1_800_000
    for acct in ("fixed_assets", "cwip", "refi.debt", "senior.debt", "refi_fee.deferred"):
        assert book.balance(acct, N - 1) == 0, acct


# ── part B ────────────────────────────────────────────────────────────────

def test_part_b_runs_clean_and_holds_the_target_dscr_every_year(resb):
    assert resb.failed_checks() == []
    w = resb.records("waterfall")
    for t in range(Y(2017), Y(2036) + 1):
        assert float(w[t]["dscr"]) == pytest.approx(1.8, abs=1e-12)
    assert resb.group.book("company").balance("refi.debt", Y(2036)) == 0


def test_q8_q9_q10_the_sculpted_refinancing(resb):
    w = resb.records("waterfall")
    assert round(w[Y(2036)]["service"]) == A["q8_target_debt_service_2036"]
    drawn = resb.loops["refi_size"].values["debt"]
    assert round(drawn) % 1000 == A["q9_amount_drawn_last_3_digits"]
    div = -resb.cut("dividends").values[Y(2016)]
    assert round(div / 1000) * 1000 == A["q10_dividend_2016_nearest_thousand"]
    # the 2016 dividend is the year's cash flow after senior debt service plus the proceeds above 9,000,000
    assert div == pytest.approx(2_040_000 - 665_000 - 500_000 + F(drawn) * F(98, 100) - 9_000_000, abs=1e-6)


def test_the_2036_target_covers_only_the_days_before_maturity(resb):
    w = resb.records("waterfall")
    cf = resb.cut("project").values[Y(2036)]
    assert w[Y(2036)]["cfads"] == cf * F(182, 366)


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_refinancing_fee_on_another_loan_refuses():
    def other(case):
        case["contracts"]["refi_fee"]["terms"]["facility"] = "senior"
    with pytest.raises(SizingError, match="fee 'refi_fee' is on 'senior', not on the new loan 'refi'"):
        variant(other)


def test_fees_that_take_the_whole_draw_refuse():
    def whole(case):
        case["contracts"]["refi_fee"]["terms"]["rate"] = 1
    with pytest.raises(SizingError, match="cannot be sized"):
        variant(whole)


def test_an_asset_register_with_a_life_and_weights_refuses():
    def both(case):
        case["rulebooks"]["assets"]["life_periods"] = 24
    with pytest.raises(InputError, match="give life_periods, weights or declining_rate"):
        variant(both)
