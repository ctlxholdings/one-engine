"""ModelOff 2015 "Tax": monthly tax at changing rates, a loss pool, tax paid in instalments (plan step 7k).

Referee: the competition's ten verified answers (Q32-Q41), with the rate
table and the 132 monthly taxable profits, as transcribed from its data
workbook and solution in
``generic_engine/tests/test_benchmark_round3_tax_loss_pools.py`` and copied
to ``cases/reference/modeloff_2015_tax.json`` by
``consolidation/experiments/make_reference_modeloff_tax.py`` (the table and
profits read from that module's constants; the schedules and answers
checked against its text). Neither workbook is in the repository, so the
transcription is not re-checked against them here. The answers are stated
to the cent; one_engine must round to exactly them.

What the case adds to the engine (convergence count 1): ``Tax`` carries a
loss pool as tax rather than as base when a policy says so
(``TaxPolicy.losses_in``): a loss then saves tax at the rate of the month
it arose in. Pack changes (not core): ``income_tax`` passes that option and
records the losses expiring each period; a ``tax_instalments`` rulebook
pays last year's tax in shares in stated months. Reused unchanged: the
offtake and operating cost contracts, the complete taxable base, the loss
pool's dated vintages and expiry, the rate by period (step 7i) and the cash
flow cut.
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
from one_engine.tax import TaxError

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_tax.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2015_tax.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = I["months"]
PROFIT = I["taxable_profit_by_month"]
TABLE = [(dt.date.fromisoformat(r["from"]), dt.date.fromisoformat(r["to"]), F(str(r["rate"]))) for r in I["rate_table"]]


def m(year, month):
    """The period of a calendar month."""
    return (year - 2015) * 12 + month - 1


def rate(t):
    """The paper's rate for month ``t``: the average of the rates in force on its days."""
    y, mo = 2015 + t // 12, 1 + t % 12
    days = [dt.date(y, mo, d) for d in range(1, calendar.monthrange(y, mo)[1] + 1)]
    return sum((next(r for a, b, r in TABLE if a <= d <= b) for d in days), F(0)) / len(days)


def instalments(name):
    return [{"period_of_year": x["month"] - 1, "share": str(x["share"])} for x in I["schedules"][name]]


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


def alt(case):
    case["rulebooks"]["instalments"]["instalments"] = instalments("alt")


def expiry(case):
    # two years, as the competition's formula counts them: a loss is still usable in the 25th month
    case["rulebooks"]["tax"]["carry_forward_periods"] = 12 * I["expiry_variant_years"] + 1


def paid(res):
    return [-x for x in res.cut("tax_paid").values]


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def res_expiry():
    return variant(expiry)


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_inputs_and_derives_the_rates():
    tax, c = CASE["rulebooks"]["tax"], CASE["contracts"]
    assert [F(x) for x in tax["rate"]] == [rate(t) for t in range(N)]
    income = [F(x) for x in c["profit"]["terms"]["price"]]
    expense = [F(x) for x in c["losses"]["terms"]["volume"]]
    assert [a - b for a, b in zip(income, expense)] == PROFIT and all(a * b == 0 for a, b in zip(income, expense))
    assert tax["losses_in"] == "tax" and tax["carry_forward_periods"] is None
    assert [(x["period_of_year"], F(x["share"])) for x in CASE["rulebooks"]["instalments"]["instalments"]] == \
        [(x["period_of_year"], F(x["share"])) for x in instalments("base")]
    assert len(PROFIT) == N == 132


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean(res):
    assert res.failed_checks() == []


def test_q32_the_rate_for_february_2022_blends_three_rates():
    r = F(CASE["rulebooks"]["tax"]["rate"][m(2022, 2)])
    assert r == F(2 * 14 + 20 * 15 + 6 * 12, 28 * 100) == F(400, 2800)


def test_q33_the_total_tax_charge_before_losses(res):
    w = res.records("tax")
    assert all(w[t]["base"] == PROFIT[t] for t in range(N))
    total = sum(PROFIT[t] * rate(t) for t in range(N))
    assert round(float(total), 2) == A["q33_total_tax_charge"]


def test_q34_the_loss_pool_at_the_end_of_2022(res):
    pool = res.records("tax")[m(2022, 12)]["losses"]
    assert round(float(pool), 2) == A["q34_loss_pool_dec_2022"]


def test_q35_the_tax_payable_to_october_2025(res):
    w = res.records("tax")
    assert round(float(sum(w[t]["tax"] for t in range(m(2025, 10) + 1))), 2) == A["q35_tax_payable_to_oct_2025"]


def test_q36_q37_the_payments(res):
    p = paid(res)
    assert round(float(p[m(2025, 8)]), 2) == A["q36_payment_aug_2025"]
    assert round(float(sum(p)), 2) == A["q37_total_paid_to_dec_2025"]
    # every year's tax is paid in full the next year, and 2025's stays payable
    w = res.records("tax")
    for y in range(2015, 2025):
        assert sum(p[m(y + 1, 1):m(y + 1, 12) + 1]) == sum(w[t]["tax"] for t in range(m(y, 1), m(y, 12) + 1))
    assert res.group.book("company").balance("tax_payable", N - 1) == sum(w[t]["tax"] for t in range(m(2025, 1), N))


def test_q38_the_alternative_schedule_paid_april_2018_to_august_2025():
    p = paid(variant(alt))
    assert round(float(sum(p[m(2018, 4):m(2025, 8) + 1])), 2) == A["q38_alt_paid_apr_2018_to_aug_2025"]


def test_q39_q40_q41_losses_that_expire_after_two_years(res_expiry):
    w = res_expiry.records("tax")
    first = next(t for t in range(N) if w[t]["expired"] > 0)
    assert f"{2015 + first // 12}-{1 + first % 12:02d}" == A["q39_first_expiry_month"]
    assert round(float(sum(w[t]["expired"] for t in range(N))), 2) == A["q40_total_expired"]
    assert round(float(sum(paid(res_expiry))), 2) == A["q41_total_paid_with_expiry"]


def test_the_pool_is_tax_generated_less_used_less_expired(res_expiry):
    w = res_expiry.records("tax")
    for t in range(N):
        charge = PROFIT[t] * rate(t)
        before = w[t - 1]["losses"] if t else 0
        used = charge - w[t]["tax"] if charge > 0 else 0
        assert w[t]["losses"] == before + max(-charge, 0) - used - w[t]["expired"]


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_pool_holds_tax_so_a_loss_saves_tax_at_the_rate_it_arose_at(res):
    def as_base(case):
        case["rulebooks"]["tax"]["losses_in"] = "base"
    other = variant(as_base)
    mine, theirs = res.records("tax"), other.records("tax")
    total = sum(mine[t]["tax"] for t in range(N))
    assert total != sum(theirs[t]["tax"] for t in range(N))
    # carried as profit, the pool at the end of 2022 is a pool of profit, about seven times the tax
    assert theirs[m(2022, 12)]["losses"] > 5 * mine[m(2022, 12)]["losses"]


def test_finding_two_years_of_expiry_means_a_loss_is_usable_in_its_25th_month():
    # the engine's own count (usable through the 24th month, gone in the 25th) changes Q39-Q41
    def strict(case):
        case["rulebooks"]["tax"]["carry_forward_periods"] = 12 * I["expiry_variant_years"]
    w = variant(strict).records("tax")
    first = next(t for t in range(N) if w[t]["expired"] > 0)
    assert first == m(2018, 9)
    assert round(float(sum(w[t]["expired"] for t in range(N))), 2) != A["q40_total_expired"]


# ── refusals ──────────────────────────────────────────────────────────────

def test_instalment_shares_that_do_not_sum_to_one_refuse():
    def short(case):
        case["rulebooks"]["instalments"]["instalments"][0]["share"] = "0.5"
    with pytest.raises(InputError, match="sum to 1"):
        variant(short)


def test_two_instalments_in_one_month_refuse():
    def twice(case):
        case["rulebooks"]["instalments"]["instalments"][1]["period_of_year"] = 3
    with pytest.raises(InputError, match="distinct periods"):
        variant(twice)


def test_an_unknown_loss_basis_refuses():
    def word(case):
        case["rulebooks"]["tax"]["losses_in"] = "profit"
    with pytest.raises(TaxError, match="losses_in"):
        variant(word)


def test_deferred_tax_on_a_pool_of_tax_refuses():
    def deferred(case):
        case["rulebooks"]["tax"]["deferred"] = {"account": "deferred_tax", "expense": "deferred_tax_expense"}
    with pytest.raises(InputError, match="losses carried as tax"):
        variant(deferred)
