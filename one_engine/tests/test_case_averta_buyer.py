"""Averta's live buyer: the portfolio bought at the exit with project-finance debt (plan step 8).

Referee: the live model itself. ``capture_averta_live.py`` records every
proforma entity run of a full report (``cases/reference/averta_live/
entities_p50.json`` and ``entities_p90.json``); the buyer is the
``build_buyer_proforma`` call: what it was given, every ledger by period
and its waterfall (CFADS, DSCR, LLCR, lock-up, distributable). The report
it feeds (``report_data.json``: ``buyer``) is the second referee.

The case file (``averta_buyer.yaml``) is written by
``make_case_averta_buyer.py``. The buyer pays the HoldCo case's exit value
(read through a one-copy cohorts generator over ``averta_holdco.yaml``)
and receives the SPVs' distributions after the hold (cohorts over
``averta_spv.yaml``, shifted by the hold).

What the case adds (convergence count): no shared-module change. Pack
changes (not core): the ``acquisition`` generator (a price, the debt share
of it, the equity); under ``flow_only``, cash a lock-up trapped is offered
again when it clears (the live distribution suspense). Reused unchanged:
offtake, operating cost, capex, the term loan, the debt reserve, sponsor
equity, the funding plan, the asset register, capital_first, the waterfall
with a DSCR lock-up, its ``active`` window, the cohorts generator, the irr
measure.

Every live number is reproduced: income, admin, debt draw, interest and
principal, depreciation and disposal by period exactly (to 1e-6); the
DSRA, the DSCR and the lock-up; the distributions. Where the live buyer
breaks an invariant the books here keep (sources equal uses, no
overdraft), the case keeps the invariant and the test reconciles the two
period by period, so each difference is a named finding with its amount.

Findings pinned here (and in the LOG):

1. The live buyer calls 5,204,468.85 of equity at acquisition for a DSRA
   that is then funded again from operating cash; the money sits idle in
   cash for 14 years (it covers the DSRA's first top-ups a quarter early)
   and comes back at maturity. Sources exceed uses by that amount.
2. The live LLCR leaves out the last quarter's CFADS (its "maturity" is the
   last period with debt outstanding at the close, one before the last
   repayment). It falls to 1.14, 0.85 and 0.00 in the last three quarters
   with the DSCR near 1.7, locks up and traps 5,335,162.28. Measured to the
   last repayment, the LLCR never falls below 1.40 (the report's own
   minimum PLCR).
3. At maturity the live S9 pays the trapped cash twice (it is in the
   opening cash and is released from suspense): it distributes 18.76M of
   13.43M held, and cash closes at -5,335,162.28. The report's "terminal
   recovery" (DSRA plus cash, -5.34M) offsets it in the reported IRR.
4. The live buyer pays no tax: its tax switch is off, though the
   configuration sets a 25% rate and depreciation lives and the code's
   docstring lists them as live.
5. The LTIP (101,904.41 to DevCo) is paid on IRR gates that cannot pass:
   the live Newton solver does not converge on a stream that is mostly
   unpaid capital and returns a stray rate. The buyer's pre-LTIP stream
   has a negative NPV at the 8% hurdle, so no part of it reaches the
   hurdle: the LTIP is nil.
6. The handover: the buyer's period 1 is the HoldCo's exit quarter, so the
   exit quarter's SPV distributions (2.80M) go to the buyer, and the buyer
   pays the price at its period 0, the HoldCo's period 20, a quarter before
   the HoldCo receives it (corrects the HoldCo step's fourth finding,
   which said those distributions reach no one).
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine import measures as M
from one_engine.cases import GENERATORS, run_case
from one_engine.inputs import Reader, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "averta_buyer.yaml"
LIVE = HERE / "reference" / "averta_live"
REPORT = json.loads((LIVE / "report_data.json").read_text())
CFG = REPORT["config"]["proforma"]
N, LAST = 57, 56
LLCR_LOCKED = (53, 54, 55)
TOL = 1e-6


def live_buyer(basis):
    calls = json.loads((LIVE / f"entities_{basis}.json").read_text())["calls"]
    return next(c for c in calls if c["caller"] == "build_buyer_proforma")


def run(basis="p50"):
    if basis == "p50":
        return run_case(CASE_PATH)
    y = REPORT["metadata"]["p90_specific_yield"]
    case = load_yaml_strict(CASE_PATH.read_text())
    case["generators"]["portfolio"]["set"] = {"generators.revenue.specific_yield": y}
    case["generators"]["holdco"]["set"] = {"generators.portfolio.set": {"generators.revenue.specific_yield": y}}
    return run_case(case)


@pytest.fixture(scope="module", params=["p50", "p90"])
def both(request):
    return request.param, run(request.param), live_buyer(request.param)


@pytest.fixture(scope="module")
def result():
    return run()


B50 = live_buyer("p50")


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def close(xs, ys, tol=TOL):
    assert len(xs) == len(ys), (len(xs), len(ys))
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst <= tol, worst


def cash(live, name):
    return live["result"]["ledgers"]["cash"]["movements"][name]


def ours_dist(r):
    """The buyer's distributions by period (the equity cash cut, contributions excluded)."""
    eq = r.cut("equity_flows").values
    return [-x if t > 0 else F(0) for t, x in enumerate(eq)]


def extra_equity(basis):
    """The live equity call's DSRA estimate: a year's peak debt service x the DSRA months."""
    b, s = CFG["buyer"], REPORT[basis]["buyer"]["scalars"]
    debt, ppy = F(str(s["pf_debt"])), 4
    amort = b["debt_tenor_years"] * ppy - b["grace_period_quarters"]
    annual = debt / amort * ppy + debt * F(str(s["pf_rate"]))
    return float(annual * b["dsra_months"] / 12)


def test_the_inputs_are_what_the_live_buyer_was_given(result):
    g, i = result.generated, B50["inputs"]
    s = REPORT["p50"]["buyer"]["scalars"]
    assert abs(float(g["acquisition"]["price"]) - s["ev"]) < TOL
    assert abs(float(g["acquisition"]["price"]) - REPORT["p50"]["valuation"]["ev"]) < TOL
    assert abs(float(g["acquisition"]["debt"]) - i["facilities"][0]["commitment"]) < TOL
    assert abs(float(g["acquisition"]["equity"]) - s["buyer_equity"]) < TOL
    close(g["acquisition"]["spend"], i["assumptions"]["capex_schedule"])
    # the HoldCo is paid that value, less its exit fee, at its exit
    fee = F(str(CFG["exit"]["fee_rate"]))
    assert g["holdco"]["exit"][21] == g["acquisition"]["price"] * (1 - fee)
    close([g["portfolio"]["distributions"][t] if t else 0 for t in range(N)], i["revenue_lines"][0]["price"], 1e-8)


def test_the_case_runs_clean_and_winds_down(both):
    _, r, _ = both
    assert r.failed_checks() == []
    assert r.rule_checks["funding.sources_equal_uses"] == []


def test_income_admin_and_debt_match_the_live_buyer_exactly(both):
    _, r, live = both
    b = r.group.book("buyer")
    close([mv(b, "spv_distributions.revenue", t) for t in range(N)], cash(live, "revenue_in"))
    close([-mv(b, "admin.expense", t) for t in range(N)], cash(live, "buyer_admin_out"))
    close([-mv(b, "senior.interest", t) for t in range(N)], cash(live, "interest_out"))
    close([mv(b, "senior.debt", t) if t == 0 else 0 for t in range(N)], cash(live, "debt_draw_in"))
    close([mv(b, "senior.debt", t) if t > 0 else 0 for t in range(N)], cash(live, "debt_repayment_out"))
    close([-mv(b, "cwip", t) if t == 0 else 0 for t in range(N)], cash(live, "capex_out"))
    L = live["result"]["ledgers"]
    close([b.balance("senior.debt", t) for t in range(N)], L["pf_senior_debt"]["closing"])


def test_the_assets_and_profit_match(both):
    _, r, live = both
    b, L = r.group.book("buyer"), live["result"]["ledgers"]
    close([b.balance("fixed_assets", t) for t in range(N)], L["fixed_assets"]["closing"])
    close([-mv(b, "depreciation", t) for t in range(N)], L["depreciation_expense"]["movements"]["book_depreciation"])
    close([-mv(b, "disposal_loss", t) for t in range(N)], L["disposal_pnl"]["movements"]["disposal_loss"])
    # no tax account moves, as in the live buyer
    assert all(x == 0 for x in L["tax_expense"]["closing"]) and "tax_expense" not in b.accounts


def test_the_dscr_and_the_lock_up_match(both):
    _, r, live = both
    w, rec, tests = live["result"]["waterfall_result"], r.records("waterfall"), r.records("test")
    close([rec[t]["dscr"] for t in range(1, N)], w["dscr"][1:], 1e-9)
    ours = {t for t in range(1, N) if tests[t]["verdict"] == "fail"}
    # the live lock-up is the DSCR's plus the LLCR's last three quarters (finding 2)
    live_locked = {t for t in range(1, N) if w["locked_up"][t]}
    assert ours == {t for t in live_locked if t not in LLCR_LOCKED}
    assert ours and all(w["dscr"][t] < CFG["buyer"]["dscr_lockup"] for t in ours)


def test_the_distributions_reconcile_with_the_live_buyer_period_by_period(both):
    basis, r, live = both
    b, L = r.group.book("buyer"), live["result"]["ledgers"]
    dist, live_dist = ours_dist(r), [-x for x in cash(live, "distribution_out")]
    extra = extra_equity(basis)
    # the equity call: the price less the debt, and the live adds its DSRA estimate (finding 1)
    assert abs(float(mv(b, "equity.capital", 0)) + extra - cash(live, "equity_in")[0]) < TOL
    # the live keeps its extra equity in cash and draws on it when a DSRA top-up exceeds the quarter's own
    # flow (finding 1); here the DSRA waits for the next quarters' flow. ``used`` is what the live has
    # drawn from that idle cash so far, ``ahead`` how far its DSRA is ahead of this one
    ahead, used = [0.0] * N, [0.0] * N
    for t in range(1, LAST - len(LLCR_LOCKED)):
        ahead[t] = L["dsra"]["closing"][t] - float(b.balance("dsra", t))
        used[t] = extra - (L["cash"]["closing"][t] - float(b.balance("cash", t)))
        assert ahead[t] >= -TOL and used[t] >= used[t - 1] - TOL, t
        # what one DSRA took and the other did not is the difference in the quarter's distribution
        assert abs(live_dist[t] - float(dist[t]) - (ahead[t - 1] - ahead[t]) - (used[t] - used[t - 1])) < TOL, t
    assert ahead[LAST - len(LLCR_LOCKED) - 1] < TOL
    # the last three quarters: the live LLCR traps what is distributed here (finding 2)
    trapped = sum(float(dist[t]) for t in LLCR_LOCKED)
    assert all(live_dist[t] == 0 for t in LLCR_LOCKED)
    assert abs(L["distribution_suspense"]["closing"][LLCR_LOCKED[-1]] - trapped) < TOL
    # maturity: the live pays its idle cash, the trapped cash, and the trapped cash again (finding 3)
    idle = L["cash"]["closing"][LLCR_LOCKED[-1]] - trapped
    assert abs(live_dist[LAST] - (float(dist[LAST]) + idle + 2 * trapped)) < TOL
    assert abs(L["cash"]["closing"][LAST] + trapped) < TOL
    if basis == "p50":
        assert round(trapped, 2) == 5335162.28 and round(extra, 2) == 5204468.85


def test_finding_the_live_llcr_leaves_out_the_last_quarter(result):
    w, L = B50["result"]["waterfall_result"], B50["result"]["ledgers"]
    cfads, debt = w["cfads"], L["pf_senior_debt"]["closing"]
    per = (CFG["rates"]["euribor"] + CFG["buyer"]["debt_spread"]) / 4

    def llcr(t, maturity):
        pv = sum(cfads[p] / (1 + per) ** (p - t) for p in range(t + 1, maturity + 1))
        return pv / debt[t]

    live_maturity = max(t for t in range(N) if debt[t] > 0)
    assert live_maturity == LAST - 1          # the last repayment is at period 56
    close([llcr(t, live_maturity) for t in range(1, LAST)], w["llcr"][1:LAST], 1e-9)
    assert [round(w["llcr"][t], 2) for t in LLCR_LOCKED] == [1.14, 0.85, 0.0]
    assert all(w["dscr"][t] > 1.65 for t in LLCR_LOCKED)
    # to the last repayment, the LLCR never falls below the 1.20 covenant (it is then the report's PLCR)
    lowest = min(llcr(t, LAST) for t in range(1, LAST))
    assert round(lowest, 2) == 1.40 and abs(lowest - REPORT["p50"]["buyer"]["scalars"]["min_plcr"]) < 1e-9


def test_finding_the_live_buyer_is_untaxed():
    a = B50["inputs"]["assumptions"]
    assert a["tax_toggle"] is False and a["corporate_tax_rate"] == CFG["buyer"]["tax_rate"] == 0.25
    assert REPORT["p50"]["buyer"]["quarterly"]["tax"] == [0.0] * (N - 1)


def _live_irr(cf, tol=1e-8, max_iter=200):
    """engine_v2.runners.run_averta._irr, as written: Newton from a payback guess, the last iterate if it fails."""
    total_out = -sum(c for c in cf if c < 0)
    total_in = sum(c for c in cf if c > 0)
    if len(cf) < 2 or total_out == 0:
        return None
    r = max(-0.5, min((total_in / total_out) ** (1.0 / len(cf)) - 1, 2.0))
    for _ in range(max_iter):
        npv = sum(c / (1 + r) ** t for t, c in enumerate(cf))
        dnpv = sum(-t * c / (1 + r) ** (t + 1) for t, c in enumerate(cf))
        if abs(dnpv) < 1e-20:
            break
        r_new = r - npv / dnpv
        if abs(r_new - r) < tol:
            return r_new
        r = r_new
    return r


def test_finding_the_ltip_is_paid_on_irr_gates_that_cannot_pass():
    q, ltip = REPORT["p50"]["buyer"]["quarterly"], CFG["ltip"]
    equity = REPORT["p50"]["buyer"]["scalars"]["buyer_equity"] + REPORT["p50"]["buyer"]["scalars"]["dsra_target"]
    d = q["distributable"]
    # the live gate, recomputed: it opens wherever the stray Newton rate clears the hurdle
    opened = []
    for t in range(1, len(d)):
        rate = _live_irr([-equity] + d[:t])
        if rate is not None and (1 + rate) ** 4 - 1 >= ltip["hurdle"]:
            opened.append(t)
    assert opened == [t for t, x in enumerate(q["ltip_rate_applied"]) if x > 0] == [2, 3, 5, 6, 7, 8]
    assert round(sum(q["ltip"]), 2) == 101904.41
    # every distribution is non-negative, so no prefix of the stream is worth more at the hurdle than the
    # whole, and the whole is worth less than nothing: the hurdle is never reached and the LTIP is nil
    per = (1 + ltip["hurdle"]) ** 0.25 - 1
    assert min(d) >= 0 and M.npv([-equity] + d, per) < 0
    assert all(M.npv([-equity] + d[:t], per) < 0 for t in range(1, len(d) + 1))


def test_finding_the_buyer_takes_over_in_the_holdco_s_exit_quarter(result):
    hold = load_yaml_strict((HERE / "averta_holdco.yaml").read_text())
    spec = dict(hold["generators"]["portfolio"])
    del spec["until"]                       # the HoldCo's own portfolio, not cut after the hold
    r = Reader(spec, source="test")
    port = GENERATORS[r.take("kind")](r)
    r.finish()
    exit_quarter = port["distributions"][21]
    b = result.group.book("buyer")
    assert mv(b, "spv_distributions.revenue", 1) == exit_quarter
    assert round(float(exit_quarter) / 1e6, 2) == 2.80
    # the HoldCo is paid at its period 21; the buyer pays at its period 0, the HoldCo's period 20
    assert result.generated["holdco"]["exit"][21] > 0 and mv(b, "cwip", 0) > 0


def test_the_report_s_buyer_returns_follow_from_the_reconciliation(result):
    """The report's IRR (1.61%) is the live cash flows with the LTIP; here 1.89%, with the findings' deltas."""
    s, q = REPORT["p50"]["buyer"]["scalars"], REPORT["p50"]["buyer"]["quarterly"]
    cf = q["cashflows"]
    assert abs(cf[0] + s["buyer_equity"] + s["dsra_target"]) < TOL
    assert abs(cf[-1] - (q["distributable_post_ltip"][-1] + s["terminal_recovery"])) < TOL
    assert abs(((1 + M.irr(cf)) ** 4 - 1) - s["irr_annual"]) < 1e-9
    assert abs(s["total_fcfe"] - float(sum(ours_dist(result)))) < TOL
    assert round(result.measure("equity_irr"), 4) == 0.0189
