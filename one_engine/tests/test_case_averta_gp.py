"""Averta's live GP: management fees, opex, carry and co-invest return, tax (plan step 8).

Referee: the live report's ``gp`` section (``cases/reference/averta_live/
report_data.json``), at P50 and P90. The live GP
(``engine_v2.runners.run_averta.build_gp``) is arithmetic, not an entity
run, so there are no live ledgers to compare: the referee is its arrays and
totals. It reads two numbers from the live HoldCo: the GP's carry and its
co-invest return (the HoldCo waterfall's ``gp_carry`` and ``gp_roc``).

Here the GP is a book (``averta_gp.yaml``, written by
``make_case_averta_gp.py``) and the two numbers are the GP's flows out of
the HoldCo case, read through a one-copy ``cohorts`` generator over
``averta_holdco.yaml``: the entities are linked by their cash, not by
restated figures.

What the case adds (convergence count): no shared-module change. Pack change
(not core): the waterfall's ``active`` window (the live GP pays nothing out
during the hold, then sweeps its cash at the exit). Reused unchanged:
offtake, operating cost, sponsor equity, income tax with unlimited loss
carry-forward, capital_first, the waterfall, the cohorts generator (over a
case that itself runs cohorts), the cash cut.

Findings pinned here (and in the LOG):

1. The return of the GP's own co-invest (772,476.77) is taxed as income:
   193,119.19 of the 1,055,992.98 tax is on a return of capital.
2. The GP's co-invest contributions to the HoldCo (772,476.77, periods 0
   to 17, the first before any fee is earned) are paid by no one in the GP:
   the live GP's cash never exceeds 265,595.96 (its fee income less opex to
   period 8), so it could not fund them.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "averta_gp.yaml"
REPORT = json.loads((HERE / "reference" / "averta_live" / "report_data.json").read_text())
CFG = REPORT["config"]["proforma"]
N, EXIT, HOLD = 22, 21, 20
HOLD_PERIODS = range(1, HOLD + 1)
TOL = 1e-6


def run(basis="p50"):
    case = load_yaml_strict(CASE_PATH.read_text())
    if basis == "p90":
        case["generators"]["holdco"]["set"] = {"generators.portfolio.set": {
            "generators.revenue.specific_yield": REPORT["metadata"]["p90_specific_yield"]}}
    return run_case(case)


@pytest.fixture(scope="module")
def result():
    return run()


@pytest.fixture(scope="module")
def holdco():
    return run_case(HERE / "averta_holdco.yaml")


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def close(xs, ys, tol=TOL):
    assert len(xs) == len(ys), (len(xs), len(ys))
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst <= tol, worst


def test_the_fee_base_is_the_holdco_s_non_warehouse_capital(holdco):
    case = load_yaml_strict(CASE_PATH.read_text())
    base = F(case["contracts"]["mgmt_fee"]["terms"]["price"])
    h = holdco.group.book("holdco")
    spent = sum(holdco.generated["capex"]["series"], F(0))
    # what the SPVs cost less what the warehouse lent: the LP's equity and loan and the GP's co-invest
    assert base == spent - h.balance("warehouse.debt", HOLD)
    assert abs(float(base) - REPORT["p50"]["holdco"]["scalars"]["total_equity"]) < TOL


def test_the_case_runs_clean_and_winds_down(result):
    assert result.failed_checks() == []


def test_the_quarterly_arrays_match_the_live_gp(result):
    b, q = result.group.book("gp"), REPORT["p50"]["gp"]["quarterly"]
    fees = [mv(b, "mgmt_fee.revenue", t) for t in HOLD_PERIODS]
    opex = [mv(b, "opex.expense", t) for t in HOLD_PERIODS]
    close(fees, q["mgmt_fees"])
    close(opex, q["opex"])
    close([f - o for f, o in zip(fees, opex)], q["net_income_ops"])
    # nothing is paid out during the hold, and nothing is earned outside it but the exit income
    assert q["distributions"] == [0.0] * HOLD
    assert all(x == 0 for t, x in enumerate(result.cut("to_topco").values) if t != EXIT)
    assert mv(b, "mgmt_fee.revenue", 0) == mv(b, "mgmt_fee.revenue", EXIT) == 0


@pytest.mark.parametrize("basis", ["p50", "p90"])
def test_the_totals_match_the_live_gp(basis, result):
    r = result if basis == "p50" else run("p90")
    b, s = r.group.book("gp"), REPORT[basis]["gp"]["scalars"]
    assert abs(float(b.balance("mgmt_fee.revenue", EXIT)) - s["total_mgmt_fees"]) < TOL
    assert abs(float(b.balance("opex.expense", EXIT)) - s["total_opex"]) < TOL
    assert abs(float(mv(b, "exit_income.revenue", EXIT)) - (s["carry"] + s["gp_coinvest_return"])) < TOL
    assert abs(float(b.balance("tax_expense", EXIT)) - s["tax"]) < TOL
    assert abs(float(-r.cut("to_topco").values[EXIT]) - s["total_to_topco"]) < TOL


def test_the_exit_income_is_the_holdco_s_carry_and_gp_capital_returned(result, holdco):
    prom = holdco.records("promote")[EXIT]
    carry = F(str(CFG["waterfall"]["promote_share"]))
    w = REPORT["p50"]["waterfall"]
    assert abs(float(prom["residual"] * carry) - w["gp_carry"]) < TOL
    assert abs(float(prom["roc_gp"]) - w["gp_roc"]) < TOL
    b = result.group.book("gp")
    assert mv(b, "exit_income.revenue", EXIT) == prom["residual"] * carry + prom["roc_gp"]


def test_tax_per_period_with_losses_carried_is_the_live_tax_on_the_total(result):
    b = result.group.book("gp")
    rate = F(str(CFG["gp"]["tax_rate"]))
    profit = [mv(b, "mgmt_fee.revenue", t) + mv(b, "exit_income.revenue", t) - mv(b, "opex.expense", t)
              for t in range(N)]
    # the live model taxes the total once; the two agree because the running profit never turns negative,
    # so every loss (Q9 to Q20) is used against the exit income
    running = [sum(profit[:t + 1], F(0)) for t in range(N)]
    assert min(running[1:]) > 0 and any(p < 0 for p in profit)
    assert b.balance("tax_expense", EXIT) == rate * running[-1]
    rec = result.records("tax")
    assert rec[HOLD]["losses"] > 0 and rec[EXIT]["losses"] == 0
    # the tax is paid once, at the exit: the payable is carried through the hold
    assert b.balance("tax_payable", HOLD) > 0 and b.balance("tax_payable", EXIT) == 0


def test_finding_the_return_of_the_gp_s_co_invest_is_taxed_as_income(result, holdco):
    roc = holdco.records("promote")[EXIT]["roc_gp"]
    rate = F(str(CFG["gp"]["tax_rate"]))
    tax = result.group.book("gp").balance("tax_expense", EXIT)
    assert round(float(rate * roc), 2) == 193119.19
    assert round(float(tax), 2) == 1055992.98
    # taxed as a return of capital, the GP would pay 862,873.79
    assert round(float(tax - rate * roc), 2) == 862873.79


def test_finding_the_gp_cannot_fund_its_co_invest(result, holdco):
    shares = {x["leg"].split(".")[0]: F(x["share"]) for x in
              load_yaml_strict((HERE / "averta_holdco.yaml").read_text())["rulebooks"]["funding"]["residual"]}
    fund = holdco.records("funding")
    paid = [fund[t]["residual"] * shares["gp"] if t in fund else F(0) for t in range(N)]
    assert abs(float(sum(paid)) - REPORT["p50"]["holdco"]["scalars"]["gp_equity"]) < TOL
    assert [t for t, x in enumerate(paid) if x] == list(range(18))
    cash = [result.group.book("gp").balance("cash", t) for t in range(N)]
    peak = max(cash[:EXIT])
    assert round(float(peak), 2) == 265595.96 and cash.index(peak) == 8
    # by Q8 the GP has been called for more than its peak cash, and by Q18 for nearly three times it
    assert sum(paid[:9]) > peak and sum(paid) > 2.9 * peak
