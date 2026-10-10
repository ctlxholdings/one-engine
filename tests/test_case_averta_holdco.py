"""Averta's live HoldCo v2: warehouse, LP shareholder loan, promote (plan step 8).

Referee: the live model itself. ``capture_averta_live.py`` records every
proforma entity run of a full report (``cases/reference/averta_live/
entities_p50.json`` and ``entities_p90.json``); the HoldCo is the
``build_holdco_v3`` call: what it was given, every ledger by period, its
waterfall and its equity result (the S10 promote and provider returns). The
report it feeds (``report_data.json``: ``holdco``, ``waterfall``, ``lp``) is
the second referee. Both are floats; the books here are exact.

The case file (``averta_holdco.yaml``) is written by
``make_case_averta_holdco.py`` from the live configuration; this test checks
each generated input against what the live HoldCo was given, then every
cash movement, ledger, promote tier and return.

What the case adds (convergence count): Portfolio and grain gains ``until``
(a cohort series nil after the hold) and the ``instalments`` generator (each
copy paid in tranches around its start), 1 core change; pack changes (not
core): the waterfall's ``flow_only`` window (the live S9 offers each
quarter's own cash flow only, never cash carried in), and the promote with
no catch-up. Reused unchanged: offtake, operating cost, capex, the loan
facility (draws as funding sources, the warehouse in cash, the SHL fully
capitalised, bullets at the exit), sponsor equity, the funding plan with
two sources and a residual split between two holders, the asset register,
``pay_as_due``, the promote, the cash cuts and the irr measure.

Findings pinned here (and in the LOG):

1. The HoldCo's cash is overdrawn by 120,769.29 from Q3 to Q20: warehouse
   interest in Q1 to Q3 exceeds the SPVs' distributions, the live S6 pays it
   anyway and S9 never makes it good (it distributes each quarter's own
   cash flow). It is repaid out of the exit.
2. Only the first quarter's purchase (6.7M) leaves CWIP; 103.65M of SPVs
   bought later stay in CWIP at the exit, matched by retained earnings. The
   exit proceeds are booked as revenue with no cost of the SPVs sold but
   the first tranche's disposal loss, so the exit quarter's profit is
   124.65M.
3. The live HoldCo is paid at period 21, a quarter after the sale: the
   buyer pays at its period 0, the HoldCo's 20 (test_case_averta_buyer.py,
   finding 6), the valuation's forward year starts after quarter 20, and
   the DevCo's exit quarter is 20. It also charges a 21st quarter of
   warehouse interest, SHL interest and admin. The report's returns move
   the exit cash to period 20 but keep those costs: LP equity IRR 29.44%,
   LP combined 24.17%, GP 62.26%, where the live S10 computes 27.70%,
   22.65% and 57.30% on its own timing. With the sale in quarter 20 and no
   21st quarter the LP's equity IRR is 30.32%
   (test_case_averta_fixed.py). First written as "the report's returns
   place the exit a quarter early", which took the HoldCo's period 21 as
   right; corrected in the fixed run.
4. The SPVs' distributions of the exit quarter (2.80M) go to the buyer,
   not the HoldCo: the HoldCo is paid for the 20 hold quarters, and the
   buyer's first quarter is the HoldCo's exit quarter, while the buyer pays
   the price a quarter earlier, at its period 0 (first written as "reach
   no one"; corrected in the buyer step).
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine import measures as M
from one_engine.cases import run_case
from one_engine.cuts import Scheme, cash_flow_cut
from one_engine.inputs import State, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "averta_holdco.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
LIVE = HERE / "reference" / "averta_live"
REPORT = json.loads((LIVE / "report_data.json").read_text())
CFG = REPORT["config"]["proforma"]
N, EXIT, HOLD = 22, 21, 20
TOL = 1e-6


def live_holdco(basis):
    calls = json.loads((LIVE / f"entities_{basis}.json").read_text())["calls"]
    return next(c for c in calls if c["caller"] == "build_holdco_v3")


H50 = live_holdco("p50")
L = H50["result"]["ledgers"]


def run(basis="p50"):
    if basis == "p50":
        return run_case(CASE_PATH)
    case = load_yaml_strict(CASE_PATH.read_text())
    case["generators"]["portfolio"]["set"] = {
        "generators.revenue.specific_yield": REPORT["metadata"]["p90_specific_yield"]}
    return run_case(case)


@pytest.fixture(scope="module")
def result():
    return run()


def close(xs, ys, tol=TOL):
    assert len(xs) == len(ys), (len(xs), len(ys))
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst <= tol, worst
    return worst


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def cash_with(book, *accounts):
    """The holdco's cash movements against the named accounts, by period."""
    scheme = Scheme("probe", {a: ("x" if a in accounts else "other") for a in book.accounts if a != "cash"})
    return cash_flow_cut(book, "cash", scheme, ["x"]).values


def holder_flows(result, holder):
    """A holder's contributions and distributions by period, gross (the cash cut nets them in a period)."""
    shares = {x["leg"].split(".")[0]: x["share"] for x in CASE["rulebooks"]["funding"]["residual"]}
    fund, prom = result.records("funding"), result.records("promote")
    carry = F(str(CFG["waterfall"]["promote_share"]))
    paid = [fund[t]["residual"] * F(shares[holder]) if t in fund else F(0) for t in range(N)]
    if holder == "lp":
        got = [prom[t]["roc_lp"] + prom[t]["pref"] + prom[t]["residual"] * (1 - carry) if t in prom else F(0)
               for t in range(N)]
    else:
        got = [prom[t]["roc_gp"] + prom[t]["catchup"] + prom[t]["residual"] * carry if t in prom else F(0)
               for t in range(N)]
    return paid, got


def gross_moic(paid, got):
    return float(sum(got) / sum(paid))


def test_the_generated_inputs_are_what_the_live_holdco_was_given(result):
    i = H50["inputs"]
    g = result.generated
    lines = {x["line_id"]: x for x in i["revenue_lines"]}
    close(g["portfolio"]["distributions"], lines["spv_distributions"]["price"], 1e-8)
    close(g["capex"]["series"] + [0, 0], i["assumptions"]["capex_schedule"], 1e-8)
    ev, fee = g["valuation"]["value"], F(str(CFG["exit"]["fee_rate"]))
    assert abs(float(ev * (1 - fee)) - lines["exit_proceeds"]["price"][EXIT]) < TOL
    assert abs(float(ev) - REPORT["p50"]["valuation"]["ev"]) < TOL
    # the generated terms enter the books as derived values
    c = result.group.contract("spv_distributions")
    assert c.terms["price"].state == State.DERIVED and c.terms["price"].source == "generator portfolio.distributions"


def test_the_case_runs_clean_and_winds_down(result):
    assert result.failed_checks() == []


def test_every_cash_movement_matches_the_live_holdco(result):
    b, m = result.group.book("holdco"), L["cash"]["movements"]
    revenue_in = cash_with(b, "spv_distributions.receivable", "exit_proceeds.receivable", "exit_fee.payable")
    close(revenue_in, m["revenue_in"])
    close(cash_with(b, "admin.payable"), m["holdco_admin_out"])
    close(cash_with(b, "cwip"), m["capex_out"])
    draws = [mv(b, "warehouse.debt", t) + max(F(0), mv(b, "shl.debt", t)) for t in range(N)]
    close([x if t < EXIT else 0 for t, x in enumerate(draws)], m["debt_draw_in"])
    close(cash_with(b, "warehouse.interest_payable"), m["interest_out"])
    close(cash_with(b, "lp.capital", "gp.capital", "lp.distributions", "gp.distributions"),
          [x + y for x, y in zip(m["equity_in"], m["distribution_out"])])
    repaid = [-x for x in cash_with(b, "warehouse.debt", "shl.debt", "shl.capitalised_interest")]
    close([repaid[t] if t == EXIT else 0 for t in range(N)], [-x for x in m["debt_repayment_out"]])
    close([b.balance("cash", t) for t in range(N)], L["cash"]["closing"])


def test_the_debt_ledgers_match(result):
    b = result.group.book("holdco")
    close([b.balance("warehouse.debt", t) for t in range(N)], L["warehouse_debt"]["closing"])
    close([b.balance("shl.debt", t) + b.balance("shl.capitalised_interest", t) for t in range(N)],
          L["shl_debt"]["closing"])
    # every quarter's SHL interest is capitalised (full PIK), the exit quarter's included
    close([mv(b, "shl.interest", t) for t in range(N)], L["shl_debt"]["movements"]["pik_interest"])
    assert all(mv(b, "shl.interest_payable", t) == 0 for t in range(N))
    close([-mv(b, "warehouse.interest", t) for t in range(N)], L["warehouse_interest_expense"]["movements"]["cash_interest"])
    close([-mv(b, "shl.interest", t) for t in range(N)], L["shl_interest_expense"]["movements"]["pik_interest"])


def test_the_assets_profit_and_equity_ledgers_match(result):
    b = result.group.book("holdco")
    for ours, theirs in (("cwip", "cwip"), ("fixed_assets", "fixed_assets")):
        close([b.balance(ours, t) for t in range(N)], L[theirs]["closing"])
    close([-mv(b, "depreciation", t) for t in range(N)], L["depreciation_expense"]["movements"]["book_depreciation"])
    close([-mv(b, "disposal_loss", t) for t in range(N)], L["disposal_pnl"]["movements"]["disposal_loss"])
    close([mv(b, "spv_distributions.revenue", t) for t in range(N)], L["revenue"]["movements"]["spv_distributions_sales"])
    # the live revenue line is the value net of the fee; here they are the revenue and an expense
    close([mv(b, "exit_proceeds.revenue", t) - mv(b, "exit_fee.expense", t) for t in range(N)],
          L["revenue"]["movements"]["exit_proceeds_sales"])
    close([-mv(b, "admin.expense", t) for t in range(N)], L["opex"]["movements"]["holdco_admin"])
    close([b.balance("lp.capital", t) + b.balance("gp.capital", t) for t in range(N)], L["contributed_capital"]["closing"])
    # retained earnings: the profit so far less the distributions out of it (not of capital)
    re, out = F(0), []
    for t in range(N):
        re += sum((mv(b, a, t) * (1 if b.accounts[a].nature.name == "INCOME" else -1)
                   for a in b.accounts if b.accounts[a].nature.name in ("INCOME", "EXPENSE")), F(0))
        re += mv(b, "lp.distributions", t) + mv(b, "gp.distributions", t)
        out.append(re)
    close(out, L["retained_earnings"]["closing"])


def test_the_promote_tiers_match_the_live_s10(result):
    p = H50["result"]["equity_result"]["promote_result"]
    rec = result.records("promote")
    get = lambda k: [rec[t][k] if t in rec else 0 for t in range(N)]
    close(get("roc_lp"), p["roc_lp"])
    close(get("roc_gp"), p["roc_gp"])
    carry = F(str(CFG["waterfall"]["promote_share"]))
    close([x * (1 - carry) for x in get("residual")], p["residual_lp"])
    close([x * carry for x in get("residual")], p["residual_gp"])
    close(holder_flows(result, "lp")[1], p["distributions_lp"])
    close(holder_flows(result, "gp")[1], p["distributions_gp"])
    assert all(x == 0 for x in get("pref")) and all(x == 0 for x in get("catchup"))


def test_the_returns_match_the_live_s10(result):
    prov = H50["result"]["equity_result"]["provider_results"]
    assert abs(result.measure("lp_equity_irr") - prov["lp_equity"]["irr_gross"]) < 1e-12
    assert abs(result.measure("gp_irr") - prov["gp_equity"]["irr_gross"]) < 1e-12
    for name, holder, cut in (("lp_equity", "lp", "lp_equity_flows"), ("gp_equity", "gp", "gp_flows")):
        close([-x for x in result.cut(cut).values], prov[name]["cf_equity_gross"])
        paid, got = holder_flows(result, holder)
        close(paid, prov[name]["contributions"])
        close(got, prov[name]["distributions_gross"])
        assert abs(gross_moic(paid, got) - prov[name]["moic"]) < 1e-12


def test_the_report_s_holdco_section(result):
    b, rep = result.group.book("holdco"), REPORT["p50"]
    s, q = rep["holdco"]["scalars"], rep["holdco"]["quarterly"]
    g = result.generated
    ev = g["valuation"]["value"]
    fee = F(str(CFG["exit"]["fee_rate"])) * ev
    shl_end = b.balance("shl.debt", EXIT - 1) + b.balance("shl.capitalised_interest", EXIT - 1) + mv(b, "shl.interest", EXIT)
    checks = {
        "total_transfer_price": sum(g["capex"]["series"]),
        "warehouse_amount": b.balance("warehouse.debt", HOLD),
        "exit_fee": fee,
        "net_exit_proceeds": ev - fee - b.balance("warehouse.debt", HOLD) - shl_end,
        "total_spv_cash": sum(g["portfolio"]["distributions"]),
        "shl_terminal_balance": shl_end,
        "lp_shl_principal": sum(max(F(0), mv(b, "shl.debt", t)) for t in range(EXIT)),
        "lp_equity": sum(holder_flows(result, "lp")[0]), "gp_equity": sum(holder_flows(result, "gp")[0]),
    }
    for k, v in checks.items():
        assert abs(float(v) - s[k]) < TOL, k
    assert abs(s["total_equity"] - s["total_transfer_price"] + s["warehouse_amount"]) < TOL
    ops = range(1, HOLD + 1)
    close([mv(b, "spv_distributions.revenue", t) for t in ops], q["spv_distributions_in"])
    close([-x for x in cash_with(b, "warehouse.interest_payable")[1:HOLD + 1]], q["warehouse_interest_out"])
    # the hold quarter's own net cash flow: the SPVs' distributions less admin and warehouse interest
    own = cash_with(b, "spv_distributions.receivable", "admin.payable", "warehouse.interest_payable")
    close(own[1:HOLD + 1], q["net_cash"])
    shl = rep["holdco"]["shl"]
    close([mv(b, "shl.capitalised_interest", t) for t in ops], shl["pik_accrual"])
    close([b.balance("shl.debt", t) + b.balance("shl.capitalised_interest", t) for t in ops], shl["closing"])
    w = rep["waterfall"]
    rec = result.records("promote")
    assert abs(float(sum(r["roc_lp"] for r in rec.values())) - w["lp_roc"]) < TOL
    assert abs(float(rec[EXIT]["residual"]) * (1 - CFG["waterfall"]["promote_share"]) - w["lp_residual"]) < TOL
    assert abs(float(rec[EXIT]["residual"]) * CFG["waterfall"]["promote_share"] - w["gp_carry"]) < TOL


def test_finding_the_holdco_cash_is_overdrawn_through_the_hold(result):
    b = result.group.book("holdco")
    cash = [b.balance("cash", t) for t in range(N)]
    assert all(c < 0 for c in cash[1:EXIT]) and cash[0] == cash[EXIT] == 0
    assert round(float(min(cash)), 2) == -120769.29 and all(c == min(cash) for c in cash[3:EXIT])
    # it is the warehouse interest the SPVs' first distributions could not cover
    shortfall = sum(min(F(0), mv(b, "spv_distributions.revenue", t) - mv(b, "admin.expense", t)
                        - mv(b, "warehouse.interest", t)) for t in range(1, 4))
    assert shortfall == min(cash)


def test_finding_spvs_bought_after_the_first_quarter_stay_in_cwip(result):
    b = result.group.book("holdco")
    first = result.generated["capex"]["series"][0]
    assert b.balance("fixed_assets", 1) + mv(b, "depreciation", 1) == first and round(float(first), 1) == 6700053.6
    stranded = b.balance("cwip", EXIT)
    assert stranded == sum(result.generated["capex"]["series"][1:]) and round(float(stranded), 1) == 103653770.4
    # the exit quarter's profit: the proceeds, less the fee, admin, interest and the first tranche's loss only
    assert round(float(L["retained_earnings"]["movements"]["net_income"][EXIT]) / 1e6, 2) == 124.65


def test_finding_the_holdco_is_paid_a_quarter_after_the_sale(result):
    lp = REPORT["p50"]["lp"]["scalars"]
    gp = REPORT["p50"]["gp"]["scalars"]

    def early(cut):
        f = [-x for x in result.cut(cut).values]
        return f[:HOLD] + [f[HOLD] + f[EXIT]]
    ann = lambda f: (1 + M.irr(f)) ** 4 - 1
    assert abs(ann(early("lp_equity_flows")) - lp["irr_equity"]) < 1e-9
    assert abs(ann(early("lp_flows")) - lp["irr_annual"]) < 1e-9
    assert abs(ann(early("gp_flows")) - gp["irr_annual"]) < 1e-9
    close(early("lp_flows"), REPORT["p50"]["lp"]["quarterly"]["cashflows"])
    # on the books' own timing
    assert (round(result.measure("lp_equity_irr"), 4), round(result.measure("lp_irr"), 4),
            round(result.measure("gp_irr"), 4)) == (0.2770, 0.2265, 0.5730)
    assert (round(lp["irr_equity"], 4), round(lp["irr_annual"], 4), round(gp["irr_annual"], 4)) == (0.2944, 0.2417, 0.6226)
    # the multiples do not depend on timing, and match: the LP's equity, and with its shareholder loan
    paid, got = holder_flows(result, "lp")
    assert abs(gross_moic(paid, got) - lp["moic_equity"]) < 1e-12
    b = result.group.book("holdco")
    shl_in = [max(F(0), mv(b, "shl.debt", t)) for t in range(EXIT)]
    shl_out = -sum(cash_with(b, "shl.debt", "shl.capitalised_interest")[EXIT:])
    assert abs(float((sum(got) + shl_out) / (sum(paid) + sum(shl_in))) - lp["moic"]) < 1e-12


def test_finding_the_exit_quarter_s_spv_distributions_go_to_the_buyer(result):
    spv = run_case(HERE / "averta_spv.yaml")
    flows = [-x for x in spv.cut("equity_flows").values]
    exit_quarter = sum(n * flows[EXIT - c] for c, n in enumerate(CFG["deployment"]["schedule_quarterly"])
                       if n and EXIT - c >= 1)
    assert result.generated["portfolio"]["distributions"][EXIT] == 0
    assert round(float(exit_quarter) / 1e6, 2) == 2.80
    # the buyer's first quarter is this one (test_case_averta_buyer.py, finding 6)
    buyer = run_case(HERE / "averta_buyer.yaml").group.book("buyer")
    assert buyer.balance("spv_distributions.revenue", 1) == exit_quarter


def test_the_p90_holdco_matches_the_live_p90_run():
    r = run("p90")
    live = live_holdco("p90")
    b, m = r.group.book("holdco"), live["result"]["ledgers"]["cash"]
    close([b.balance("cash", t) for t in range(N)], m["closing"])
    close(cash_with(b, "lp.capital", "gp.capital", "lp.distributions", "gp.distributions"),
          [x + y for x, y in zip(m["movements"]["equity_in"], m["movements"]["distribution_out"])])
    prov = live["result"]["equity_result"]["provider_results"]
    assert abs(r.measure("lp_equity_irr") - prov["lp_equity"]["irr_gross"]) < 1e-11
    assert abs(r.measure("gp_irr") - prov["gp_equity"]["irr_gross"]) < 1e-11
    assert r.failed_checks() == []
