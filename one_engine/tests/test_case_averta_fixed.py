"""Averta with the live model's bugs fixed: the headline numbers on corrected books (plan step 8).

The reproduction (``test_case_averta_*.py``) runs the live Averta chain on
one_engine and pins every live bug as a finding with its amount. Here the
same cases run with those bugs fixed, to show how the headline numbers move.
Each fixed case (``averta_<entity>_fixed.yaml``) is the reproduced case
with named fixes applied, written by ``make_case_averta_fixed.py``; each
file's header lists its fixes, each answering a finding in the LOG. The
script also writes the bridge (each headline after each fix,
``reference/averta_fixed_bridge.json``); this file checks its two ends
against the report and against the fixed cases.

The fixes:

* HoldCo: the sale closes at the end of quarter 20, when the buyer pays and
  the valuation's forward year starts (the live HoldCo alone receives the
  price at 21 and carries a 21st quarter of costs); the LP and the GP fund
  the HoldCo's shortfalls as they arise (the live overdraft); the SPVs are
  held at cost and derecognised at the sale.
* GP: TopCo funds the GP's co-invest as the HoldCo calls it; its cost is set
  against its return for tax.
* Buyer: the SPVs are held to the end of their 100-quarter life (the live
  buyer stops at its debt's maturity, with no terminal value). Its other
  live bugs (idle equity, the LLCR, the double payment, the LTIP) are
  already corrected in the reproduced buyer.
* DevCo: TopCo covers its shortfalls and receives its surplus every quarter
  (the live overdraft, the exit quarter lost); losses are carried forward;
  development spend due before period 0 is paid at period 0.
* TopCo: its shareholders' cash by quarter (the co-invest and the DevCo's
  shortfalls it funds, the GP's sweep, the DevCo's surplus, admin every
  quarter), valued at 8% (the live rate), in place of the live sum of cash
  and a present value.

Not changed, each a judgment the live configuration does not settle: the
buyer stays untaxed (its income is the SPVs' dividends, already taxed in
the SPVs; whether they are taxed again depends on its jurisdiction), and the
HoldCo stays untaxed, as live.

What it adds (convergence count): one core change, the phase in a
rulebook leg's event id (a holder may pay in twice in one period, a
construction call and a shortfall); one pack rulebook, ``deficit_funding``.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
REPORT = json.loads((HERE / "reference" / "averta_live" / "report_data.json").read_text())
BRIDGE = json.loads((HERE / "reference" / "averta_fixed_bridge.json").read_text())
CFG = REPORT["config"]["proforma"]
EXIT, PPY = 20, 4
RATE = F(str(REPORT["p50"]["post_exit_am"]["discount_rate"]))
TOL = 1e-6


def load(name):
    return load_yaml_strict((HERE / name).read_text())


def at_p90(case):
    """The case at the P90 specific yield: every SPV run it reads, directly or through the HoldCo."""
    y = REPORT["metadata"]["p90_specific_yield"]
    for g in case.get("generators", {}).values():
        t = g.get("template", "")
        if t == "averta_spv.yaml":
            g["set"] = {"generators.revenue.specific_yield": y}
        elif t.startswith("averta_holdco"):
            g["set"] = {"generators.portfolio.set": {"generators.revenue.specific_yield": y}}
    return case


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def called(result, *names):
    """What deficit_funding rulebooks called, by period."""
    out = [F(0)] * result.group.periods
    for n in names:
        for t, rec in result.records(n).items():
            out[t] += rec["called"]
    return out


def value(flows, at):
    """The flows' value at period ``at``, at the live 8% a year compounded quarterly."""
    q = (1 + float(RATE)) ** (1 / PPY)
    return sum(float(x) * q ** (at - t) for t, x in enumerate(flows))


def topco_flows(gp, devco):
    """TopCo's net cash by quarter, which its shareholders receive (or cover): the GP's, the DevCo's, less admin."""
    g = [-x for x in gp.cut("to_topco").values]
    d = [-x for x in devco.cut("to_topco").values]
    admin = F(CFG["topco"]["admin_annual"]) / PPY
    return [(g[t] if t < len(g) else 0) + d[t] - admin for t in range(len(d))]


@pytest.fixture(scope="module")
def holdco():
    return run_case(HERE / "averta_holdco_fixed.yaml")


@pytest.fixture(scope="module")
def gp():
    return run_case(HERE / "averta_gp_fixed.yaml")


@pytest.fixture(scope="module")
def buyer():
    return run_case(HERE / "averta_buyer_fixed.yaml")


@pytest.fixture(scope="module")
def devco():
    return run_case(HERE / "averta_devco_fixed.yaml")


@pytest.mark.parametrize("entity", ["holdco", "gp", "buyer", "devco"])
def test_every_fixed_case_runs_clean_with_its_cash_floored(entity):
    case = load(f"averta_{entity}_fixed.yaml")
    assert f"{entity}.cash" in case["floors"]
    assert run_case(case).failed_checks() == []


def test_deficit_funding_needs_a_floor_to_fund_up_to():
    case = load("averta_holdco_fixed.yaml")
    case["floors"] = {}
    with pytest.raises(InputError, match="no floor to fund up to"):
        run_case(case)


# ── HoldCo ────────────────────────────────────────────────────────────────

def test_the_holdco_is_paid_in_the_quarter_the_buyer_pays(holdco, buyer):
    b = holdco.group.book("holdco")
    assert holdco.group.periods == EXIT + 1
    ev = holdco.generated["valuation"]["value"]
    assert mv(b, "exit_proceeds.revenue", EXIT) == ev
    # the buyer pays it at its period 0, the HoldCo's quarter 20 (test_case_averta_buyer.py, finding 6)
    assert buyer.group.book("buyer").balance("cwip", 0) == ev == buyer.generated["acquisition"]["price"]
    # both loans are repaid at the sale, and no 21st quarter of interest is charged
    assert b.balance("warehouse.debt", EXIT) == b.balance("shl.debt", EXIT) == 0
    assert b.balance("shl.capitalised_interest", EXIT) == 0


def test_the_holdco_s_shortfalls_are_called_from_the_lp_and_the_gp(holdco):
    b = holdco.group.book("holdco")
    assert min(b.balance("cash", t) for t in range(EXIT + 1)) >= 0
    calls = called(holdco, "support_operate", "support_pay")
    # the live overdraft, called as it arises: Q1 to Q3, where admin and interest exceed the SPVs' distributions
    assert [t for t, x in enumerate(calls) if x] == [1, 2, 3]
    assert round(float(sum(calls)), 2) == 120769.29
    live = run_case(HERE / "averta_holdco.yaml").group.book("holdco")
    assert sum(calls) == -min(live.balance("cash", t) for t in range(EXIT + 1))


def test_the_spvs_are_held_at_cost_and_derecognised_at_the_sale(holdco):
    b = holdco.group.book("holdco")
    assert b.balance("cwip", EXIT) == b.balance("fixed_assets", EXIT) == 0
    assert mv(b, "disposal_loss", EXIT) == sum(holdco.generated["capex"]["series"])
    assert all(mv(b, "depreciation", t) == 0 for t in range(EXIT + 1))


def test_the_fixed_holdco_s_returns(holdco):
    got = tuple(round(holdco.measure(k), 4) for k in ("lp_equity_irr", "lp_irr", "gp_irr"))
    assert got == (0.3013, 0.2457, 0.6346)
    lp, gp = REPORT["p50"]["lp"]["scalars"], REPORT["p50"]["gp"]["scalars"]
    # above the report's (29.44%, 24.17%, 62.26%): the report moved the exit cash to quarter 20 but kept
    # the 21st quarter of costs the live HoldCo charges
    assert (lp["irr_equity"], lp["irr_annual"], gp["irr_annual"]) < (got[0], got[1], got[2])


# ── GP ────────────────────────────────────────────────────────────────────

def test_the_gp_pays_its_co_invest_and_topco_funds_it_as_called(gp, holdco):
    b = gp.group.book("gp")
    calls = holdco.cut("gp_flows").values[:EXIT]           # cash into the HoldCo: the GP's calls
    assert all(x >= 0 for x in calls)
    assert [mv(b, "cwip", t) for t in range(EXIT)] == calls
    # TopCo pays in exactly the call, in its quarter
    assert gp.cut("to_topco").values[:EXIT] == calls
    # the co-invest: the live calls (772,476.77) and the GP's share of the HoldCo's shortfall calls
    share = F(load("averta_holdco_fixed.yaml")["rulebooks"]["support_pay"]["residual"][1]["share"])
    live = run_case(HERE / "averta_holdco.yaml")
    live_calls = sum(live.cut("gp_flows").values[:EXIT], F(0))
    assert round(float(live_calls), 2) == 772476.77
    assert sum(calls) == live_calls + share * sum(called(holdco, "support_operate", "support_pay"))


def test_only_the_gain_on_the_co_invest_is_taxed(gp):
    b = gp.group.book("gp")
    fees = b.balance("mgmt_fee.revenue", EXIT)
    opex = b.balance("opex.expense", EXIT)
    proceeds = b.balance("exit_income.revenue", EXIT)
    cost = b.balance("coinvest_cost", EXIT)
    assert cost == b.balance("cwip", EXIT - 1) and b.balance("coinvest_depreciation", EXIT) == 0
    assert b.balance("tax_expense", EXIT) == F(1, 4) * (fees - opex + proceeds - cost)


def test_the_fixed_gp_s_cash_to_topco(gp):
    assert round(float(-sum(gp.cut("to_topco").values)), 2) == 2693383.51


# ── buyer ─────────────────────────────────────────────────────────────────

def test_the_buyer_holds_the_spvs_to_the_end_of_their_life(buyer):
    live = run_case(HERE / "averta_buyer.yaml")
    n = buyer.group.periods
    # the last cohort (deployed in quarter 15) ends at its template's quarter 99, the buyer's 94
    assert n == 95
    b, lb = buyer.group.book("buyer"), live.group.book("buyer")
    income = [mv(b, "spv_distributions.revenue", t) for t in range(n)]
    assert income[:57] == [mv(lb, "spv_distributions.revenue", t) for t in range(57)]
    assert all(x > 0 for x in income[57:94])
    # before the live horizon's last quarter the equity's cash is the reproduced buyer's
    assert buyer.cut("equity_flows").values[:56] == live.cut("equity_flows").values[:56]
    assert round(buyer.measure("equity_irr"), 4) == 0.0986


# ── DevCo ─────────────────────────────────────────────────────────────────

def test_topco_covers_the_devco_s_shortfalls(devco):
    b = devco.group.book("devco")
    n = devco.group.periods
    assert min(b.balance("cash", t) for t in range(n)) >= 0
    calls = called(devco, "support")
    running = [sum(calls[:t + 1], F(0)) for t in range(n)]
    assert running[-1] > 0 and all(t < EXIT for t, x in enumerate(calls) if x)


def test_every_quarter_of_the_devco_reaches_topco_once(devco):
    b = devco.group.book("devco")
    to = [-x for x in devco.cut("to_topco").values]
    ni = devco.cut("net_income").values
    # no receivables or payables are left, so what TopCo gets over the life is the net income, the exit quarter's too
    assert sum(to, F(0)) == sum(ni, F(0))
    assert to[EXIT] == ni[EXIT] and b.balance("topco.capital", devco.group.periods - 1) == 0


def test_the_devco_s_losses_are_carried_and_its_early_spend_is_charged(devco):
    b = devco.group.book("devco")
    assert b.balance("tax_expense", EXIT - 1) == 0
    d = CFG["devco"]
    devex = F(d["dead_deal_cost"]) * (1 / F(str(d["success_rate"])) - 1) + F(d["dev_cost_per_project"])
    assert b.balance("pipeline.expense", devco.group.periods - 1) == devex * CFG["deployment"]["total_projects"]


# ── TopCo and the bridge ──────────────────────────────────────────────────

def test_the_fixed_topco(gp, devco):
    flows = topco_flows(gp, devco)
    running = [sum(flows[:t + 1], F(0)) for t in range(len(flows))]
    assert round(value(flows, EXIT), 2) == 3991860.50
    assert round(float(sum(flows)), 2) == 7466162.48
    # its shareholders put in up to 2.45M before the exit: the co-invest, the DevCo's shortfalls, admin
    assert round(float(-min(running)), 2) == 2451700.49 and running.index(min(running)) < EXIT
    assert REPORT["p50"]["topco"]["distributable"] > value(flows, EXIT)


def test_the_bridge_ends_are_the_report_and_the_fixed_cases(holdco, gp, buyer, devco):
    ends = {k: (dict(v[0][1]), dict(v[-1][1])) for k, v in BRIDGE.items()}
    lp, gps, by = REPORT["p50"]["lp"]["scalars"], REPORT["p50"]["gp"]["scalars"], REPORT["p50"]["buyer"]["scalars"]
    assert ends["holdco"][0] == {"lp_equity_irr": lp["irr_equity"], "lp_irr": lp["irr_annual"], "gp_irr": gps["irr_annual"]}
    assert ends["holdco"][1] == {k: holdco.measure(k) for k in ("lp_equity_irr", "lp_irr", "gp_irr")}
    assert ends["gp"][0]["to_topco"] == REPORT["p50"]["topco"]["gp_inflow"]
    assert ends["gp"][1]["to_topco"] == float(-sum(gp.cut("to_topco").values, F(0)))
    assert ends["buyer"][0]["equity_irr"] == by["irr_annual"] and ends["buyer"][1]["equity_irr"] == buyer.measure("equity_irr")
    assert ends["devco"][1]["to_topco"] == float(-sum(devco.cut("to_topco").values, F(0)))
    assert ends["topco"][0] == {"distributable": REPORT["p50"]["topco"]["distributable"]}
    assert abs(ends["topco"][1]["value_at_exit"] - value(topco_flows(gp, devco), EXIT)) < TOL


def test_the_fixed_chain_at_p90(devco):
    h = run_case(at_p90(load("averta_holdco_fixed.yaml")))
    g = run_case(at_p90(load("averta_gp_fixed.yaml")))
    b = run_case(at_p90(load("averta_buyer_fixed.yaml")))
    assert h.failed_checks() == g.failed_checks() == b.failed_checks() == []
    assert tuple(round(h.measure(k), 4) for k in ("lp_equity_irr", "lp_irr", "gp_irr")) == (0.2865, 0.2351, 0.6181)
    assert round(b.measure("equity_irr"), 4) == 0.0881
    assert round(value(topco_flows(g, devco), EXIT), 2) == 3879665.00
