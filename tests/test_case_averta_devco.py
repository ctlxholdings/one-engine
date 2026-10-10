"""Averta's live DevCo: development fees, asset management, overhead, pipeline costs, tax (plan step 8).

Referee: the live report's ``devco`` section (the 20 hold quarters) and
``post_exit_am`` section (the 20 years after the exit), at P50. The live
DevCo (``engine_v2.runners.run_averta.build_devco_proforma``) computes its
arrays by hand over 101 quarters and never runs its entity, so its arrays
and totals are the referee; there are no live ledgers.

Here the DevCo is a book (``averta_devco.yaml``, written by
``make_case_averta_devco.py``) on the deployment calendar: quarters 0 to 19
the hold, 20 the exit quarter, 21 to 100 the post-exit years.

What the case adds (convergence count): no shared-module change, no pack
change. Reused unchanged: offtake (a series price), operating cost (an
index series, a volume series), income tax with no loss carried (a
carry-forward of one period), the waterfall paying only tax under
``flow_only``, the pnl cut.

Findings pinned here (and in the LOG):

1. The DevCo's cash is overdrawn through the hold, to -1,372,768.75 at
   quarter 12, and nothing funds it: the 100,000 seed equity of the
   configuration is never paid in.
2. The first three cohorts' pipeline costs are clamped at quarter 0: the
   quarters before it are dropped, 367,500 of development spend that is
   never charged.
3. No loss is carried forward: the DevCo pays 319,412.81 of tax in the hold
   on a cumulative loss of 414,530.33. With losses carried forward it would
   pay none in the hold.
4. The exit quarter (quarter 20) is in neither the hold total nor the
   post-exit NPV that TopCo receives: its net income of 55,723.01 is lost.
5. The post-exit NPV (2,605,665.00) includes the buyer's LTIP after tax,
   which is nil when its hurdle is measured correctly (buyer finding 5); it
   also books each LTIP payment a quarter late. Without it the NPV is
   2,543,193.18.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "averta_devco.yaml"
REPORT = json.loads((HERE / "reference" / "averta_live" / "report_data.json").read_text())
CFG = REPORT["config"]["proforma"]
DEV, POST = REPORT["p50"]["devco"], REPORT["p50"]["post_exit_am"]
HOLD, EXIT, N, PPY = 20, 20, 101, 4
TOL = 1e-6


@pytest.fixture(scope="module")
def result():
    return run_case(CASE_PATH)


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def close(xs, ys, tol=TOL):
    assert len(xs) == len(ys), (len(xs), len(ys))
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst <= tol, worst


def post_exit_years(values):
    """The 80 post-exit quarters summed by post-exit year, as the live model buckets them."""
    return [sum(values[EXIT + 1 + PPY * y:EXIT + 1 + PPY * (y + 1)], F(0)) for y in range(20)]


def test_the_case_runs_clean(result):
    assert result.failed_checks() == []


def test_the_hold_quarters_match_the_live_devco(result):
    b, q = result.group.book("devco"), DEV["quarterly"]
    hold = range(HOLD)
    close([mv(b, "dev_fees.revenue", t) for t in hold], q["revenue_dev_fees"])
    close([mv(b, "am_fees.revenue", t) for t in hold], q["revenue_am"])
    close([mv(b, "am_costs.expense", t) for t in hold], q["cost_am_total"])
    close([mv(b, "overhead.expense", t) for t in hold], q["cost_overhead"])
    close([mv(b, "pipeline.expense", t) for t in hold], q["cost_pipeline"])
    close([mv(b, "tax_expense", t) for t in hold], q["tax"])
    close(result.cut("net_income").values[:HOLD], q["net_income"])
    close([b.balance("cash", t) for t in hold], q["cash_balance"])
    s = DEV["scalars"]
    assert abs(float(sum(result.cut("net_income").values[:HOLD])) - s["cumulative_net_income"]) < TOL
    assert abs(float(b.balance("dev_fees.revenue", N - 1)) - s["total_dev_fee"]) < TOL


def test_the_post_exit_years_match_the_live_devco_but_for_the_ltip(result):
    b = result.group.book("devco")
    years = lambda acct: post_exit_years([mv(b, acct, t) for t in range(N)])
    close(years("am_fees.revenue"), POST["annual_am_revenue"])
    close(years("am_costs.expense"), POST["annual_am_cost"])
    close(years("post_exit_overhead.expense"), POST["annual_overhead"])
    rate = 1 - F(str(CFG["devco"]["corporate_tax_rate"]))
    ltip = POST["annual_ltip_revenue"]
    # each live year is this one plus its LTIP after tax (every post-exit year is profitable)
    close([x + rate * F(str(l)) for x, l in zip(post_exit_years(result.cut("net_income").values), ltip)],
          POST["annual_cashflows"])
    close([x + F(str(l)) / 4 for x, l in zip(years("tax_expense"), ltip)], POST["annual_tax"])


def test_finding_the_devco_overdraft_is_never_funded(result):
    b = result.group.book("devco")
    cash = [b.balance("cash", t) for t in range(N)]
    low = min(cash[:HOLD])
    assert round(float(low), 2) == round(DEV["scalars"]["min_cash_balance"], 2) == -1372768.75
    assert cash.index(low) == 12
    # no equity is paid in: the cash is the running profit after tax, from the first quarter
    assert "seed_equity" in CFG["devco"] and not any(a.endswith(".capital") for a in b.accounts)
    assert all(cash[t] == sum(result.cut("net_income").values[:t + 1], F(0)) for t in range(N))


def test_finding_the_first_cohorts_pipeline_costs_are_clamped(result):
    d = CFG["devco"]
    devex = F(d["dead_deal_cost"]) * (1 / F(str(d["success_rate"])) - 1) + F(d["dev_cost_per_project"])
    schedule = CFG["deployment"]["schedule_quarterly"]
    charged = result.group.book("devco").balance("pipeline.expense", N - 1)
    assert devex * sum(schedule) - charged == 367500


def test_finding_no_loss_is_carried_forward(result):
    q = DEV["quarterly"]
    assert round(sum(q["tax"]), 2) == 319412.81 and round(sum(q["net_income"]), 2) == -414530.33
    case = load_yaml_strict(CASE_PATH.read_text())
    case["rulebooks"]["tax"]["carry_forward_periods"] = None
    carried = run_case(case).group.book("devco")
    assert carried.balance("tax_expense", HOLD - 1) == 0


def test_finding_the_exit_quarter_reaches_neither_the_hold_nor_the_npv(result):
    ni = result.cut("net_income").values
    assert round(float(ni[EXIT]), 2) == 55723.01
    hold, post = sum(ni[:HOLD], F(0)), sum(ni[EXIT + 1:], F(0))
    assert abs(float(hold) - DEV["scalars"]["cumulative_net_income"]) < TOL
    rate = 1 - F(str(CFG["devco"]["corporate_tax_rate"]))
    assert abs(float(post + rate * F(str(POST["ltip_nominal"]))) - POST["nominal"]) < TOL


def test_finding_the_post_exit_npv_carries_the_ltip(result):
    rate = 1 - F(str(CFG["devco"]["corporate_tax_rate"]))
    disc = 1 + F(str(POST["discount_rate"]))
    years = post_exit_years(result.cut("net_income").values)
    npv = sum(x / disc ** (y + 1) for y, x in enumerate(years))
    ltip = sum(rate * F(str(l)) / disc ** (y + 1) for y, l in enumerate(POST["annual_ltip_revenue"]))
    assert abs(float(npv + ltip) - POST["npv"]) < TOL
    assert round(float(npv), 2) == 2543193.18 and round(POST["npv"], 2) == 2605665.00
    # the live adds the buyer's LTIP of its operating quarter k (the calendar's quarter exit + k) at
    # exit + 1 + k, a quarter late: k = 8, calendar 28, the second post-exit year, lands in 29, the third
    q = REPORT["p50"]["buyer"]["quarterly"]["ltip"]
    assert [k for k, x in enumerate(q) if x] == [2, 3, 8]
    assert abs(POST["annual_ltip_revenue"][0] - (q[2] + q[3])) < TOL
    assert POST["annual_ltip_revenue"][1] == 0 and abs(POST["annual_ltip_revenue"][2] - q[8]) < TOL
