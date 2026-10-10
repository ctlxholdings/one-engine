"""ModelOff 2013 "Hard Times": a 36-month cash forecast for a toy maker (plan step 7l).

Referee: the competition's answers, with the assumptions, as transcribed
from its assumptions workbook in
``generic_engine/tests/test_benchmark_round3_hard_times.py`` and copied to
``cases/reference/modeloff_2013_hard_times.json`` by
``consolidation/experiments/make_reference_modeloff_hard_times.py``
(numbers read from that module's constants, answers checked against its
text). The workbook is not in the repository, so the transcription is not
re-checked against it here. Eight of the nine answers are reproduced, to
the stated rounding. Q7 (a target debtor balance for 43 debtor days) is
not: the transcription could not recover the competition's annualisation
and only pins the answer. The conventions tried here (trailing twelve
months, nine months annualised, the half-year or the quarter annualised,
365 or 360 days) do not give it either.

What the case adds to the engine (convergence count 0): no shared module
changed. Pack changes (not core): ``payment_days`` of the offtake and
operating cost contracts may be a payment profile; ``receivable_run_off``
and ``payable_run_off`` settle balances carried in on the opening balance
sheet over a profile; a ``pay_as_due`` rulebook pays listed claims in full
whatever the cash. Reused unchanged: the offtake, operating cost and loan
contracts (a stated principal schedule, interest on the opening balance),
the opening balance sheet, the asset register's opening tranche and the
cash flow cut.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_hard_times.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2013_hard_times.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = I["months"]
PRICE = F(str(I["price"]))
UNIT_COST = PRICE * (1 - F(str(I["contribution_margin"])))


def ym(t):
    """Calendar (year, month) of period ``t``: period 0 is October 2013."""
    return 2013 + (t + 9) // 12, 1 + (t + 9) % 12


def p(y, mo):
    return (y - 2013) * 12 + mo - 10


def year(y):
    return [t for t in range(N) if ym(t)[0] == y]


def units(t):
    y, mo = ym(t)
    u = F(I["units_2013"])
    for k in range(2014, y + 1):
        u *= 1 + F(str(I["unit_growth"][str(k)]))
    return u * F(str(I["quarter_share_of_year"][str((mo - 1) // 3 + 1)])) / 3


def indirect(t):
    return F(I["indirect_2013"]) * (1 + F(str(I["indirect_growth"]))) ** (ym(t)[0] - 2013) / 12


def lagged(amounts, profile, opening, opening_profile):
    """The paper's cash: each month's amount paid on the profile, plus the opening balance on its own."""
    out = [F(0)] * N
    for t, x in enumerate(amounts):
        for k, s in enumerate(profile):
            if t + k < N:
                out[t + k] += x * F(str(s))
    for k, s in enumerate(opening_profile):
        out[k] += opening * F(str(s))
    return out


def paper(price=lambda t: PRICE, volume=lambda t: 1):
    """The paper's closing cash and payables by month, recomputed from the inputs."""
    sales = [units(t) * volume(t) * price(t) for t in range(N)]
    buys = [units(t) * volume(t) * UNIT_COST + indirect(t) for t in range(N)]
    o = I["opening"]
    receipts = lagged(sales, I["receipt_profile"], o["receivables"], I["opening_receivables_profile"])
    payments = lagged(buys, I["payment_profile"], o["payables"], I["opening_payables_profile"])
    amort = {a["month"]: a["amount"] for a in I["amortisation"]}
    debt, cash, payables, out = F(o["debt"]), F(o["cash"]), F(o["payables"]), []
    for t in range(N):
        y, mo = ym(t)
        interest = debt * F(str(I["debt_rate"])) / 12
        repay = amort.get(f"{y}-{mo:02d}", 0)
        cash += receipts[t] - payments[t] - repay - interest
        payables += buys[t] - payments[t]
        debt -= repay
        out.append({"cash": cash, "payables": payables, "interest": interest})
    return out


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("company")


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_inputs():
    c = CASE["contracts"]
    assert [F(x) for x in c["sales"]["terms"]["volume"]] == [units(t) for t in range(N)]
    assert c["direct"]["terms"]["volume"] == c["sales"]["terms"]["volume"]
    assert F(c["sales"]["terms"]["price"]) == PRICE and F(c["direct"]["terms"]["unit_rate"]) == UNIT_COST
    assert [F(c["indirect"]["terms"]["annual"]) * F(x) / 12 for x in c["indirect"]["terms"]["index"]] == \
        [indirect(t) for t in range(N)]
    assert [F(x) for x in c["sales"]["terms"]["payment_days"]] == [F(str(x)) for x in I["receipt_profile"]]
    assert [F(x) for x in c["direct"]["terms"]["payment_days"]] == [F(str(x)) for x in I["payment_profile"]]
    assert c["indirect"]["terms"]["payment_days"] == c["direct"]["terms"]["payment_days"]
    amort = {a["month"]: a["amount"] for a in I["amortisation"]}
    assert c["debt"]["terms"]["principal_schedule"] == [amort.get("%d-%02d" % ym(t), 0) for t in range(N)]
    opening = {k: v["value"] for k, v in CASE["opening"]["company"].items()}
    o = I["opening"]
    assert (opening["cash"], opening["opening_receivables"], opening["opening_payables"], opening["fixed_assets"],
            opening["debt.debt"]) == (o["cash"], o["receivables"], o["payables"], o["ppe"], o["debt"])


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_reproduces_the_papers_cash_every_month(res, book):
    assert res.failed_checks() == []
    ref = paper()
    for t in range(N):
        assert book.balance("cash", t) == ref[t]["cash"]


def test_q1_revenue_in_january_2014(book):
    x = mv(book, "sales.revenue", p(2014, 1))
    assert x == units(p(2014, 1)) * PRICE
    assert round(x) == A["q1_revenue_jan_2014"]


def test_q2_payables_at_the_end_of_2015(book):
    t = p(2015, 12)
    x = sum(book.balance(a, t) for a in ("direct.payable", "indirect.payable", "opening_payables"))
    assert x == paper()[t]["payables"]
    assert round(x) == A["q2_payables_dec_2015"]


def test_q3_indirect_costs_over_the_forecast(book):
    x = sum(mv(book, "indirect.expense", t) for t in range(N))
    assert x == sum(indirect(t) for t in range(N))
    assert round(x) == A["q3_indirect_total"]


def test_q4_interest_in_2014(book):
    x = sum(mv(book, "debt.interest", t) for t in year(2014))
    assert x == sum(paper()[t]["interest"] for t in year(2014))
    assert round(x) == A["q4_interest_2014"]


def test_q5_cash_runs_out_in_june_2014(book):
    first = next(t for t in range(N) if book.balance("cash", t) < 0)
    assert "%d-%02d" % ym(first) == A["q5_first_negative_cash_month"]


def test_q6_the_shortfall_peaks_in_the_last_quarter_of_2015(book):
    worst = min(range(N), key=lambda t: book.balance("cash", t))
    y, mo = ym(worst)
    assert f"{y}-Q{(mo - 1) // 3 + 1}" == A["q6_peak_shortfall_quarter"]


def test_q8_interest_cover_in_2015(book):
    ebit = sum(mv(book, "sales.revenue", t) - mv(book, "direct.expense", t) - mv(book, "indirect.expense", t)
               - mv(book, "depreciation", t) for t in year(2015))
    cover = ebit / sum(mv(book, "debt.interest", t) for t in year(2015))
    assert round(float(cover), 1) == A["q8_interest_cover_2015"]


def test_q9_cash_at_the_end_of_2015_with_higher_prices_and_lower_volumes():
    s = I["sensitivity_q9"]
    on = (lambda t: ym(t)[0] >= s["from_year"])
    price = (lambda t: PRICE * F(str(s["price_factor"])) if on(t) else PRICE)
    volume = (lambda t: F(str(s["volume_factor"])) if on(t) else 1)

    def sensitivity(case):
        c = case["contracts"]
        c["sales"]["terms"]["price"] = [price(t) for t in range(N)]
        for k in ("sales", "direct"):          # direct cost per unit is kept
            c[k]["terms"]["volume"] = [units(t) * volume(t) for t in range(N)]
    cash = variant(sensitivity).group.book("company").balance("cash", p(2015, 12))
    assert cash == paper(price, volume)[p(2015, 12)]["cash"]
    assert round(cash) == A["q9_sensitivity_cash_dec_2015"]


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_opening_balances_are_settled_in_three_months_and_kept_apart(book):
    assert [book.balance("opening_receivables", t) for t in range(3)] == [420_000, 60_000, 0]
    assert [book.balance("opening_payables", t) for t in range(3)] == [245_000, 35_000, 0]


def test_finding_debt_service_is_paid_with_cash_the_company_does_not_have(book):
    t = p(2015, 12)
    assert book.balance("cash", t) < 0 and mv(book, "debt.debt", t) == -150_000
    assert book.balance("debt.debt", N - 1) == 1_000_000          # half the debt falls due after the grid


def test_finding_receivables_outlast_the_grid(book):
    # nothing is collected early in the last month: September 2016's sales are still owed
    assert book.balance("sales.receivable", N - 1) == sum(
        units(t) * PRICE * k for t, k in ((N - 1, 1), (N - 2, F(40, 100)), (N - 3, F(15, 100))))


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_payment_profile_that_does_not_sum_to_one_refuses():
    def short(case):
        case["contracts"]["sales"]["terms"]["payment_days"] = [0, "0.6", "0.25"]
    with pytest.raises(ValueError, match="sum to 1"):
        variant(short)


def test_a_run_off_profile_that_does_not_sum_to_one_refuses():
    def short(case):
        case["contracts"]["opening_payables"]["terms"]["profile"] = ["0.3", "0.6"]
    with pytest.raises(ValueError, match="sum to 1"):
        variant(short)
