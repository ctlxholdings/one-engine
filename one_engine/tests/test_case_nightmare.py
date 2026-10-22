"""The 500MW nightmare (plan step 7): anchors, parity, documented divergences.

Same referees as the 100MW and 50MW cases: hand-computed anchors, parity
with the stored old-engine run where it is sound
(``cases/reference/nightmare_old_engine.json``), and each divergence shown
to be a documented old-engine defect. The nightmare has too many of those
for one sponsor-total reconciliation to say anything, so each is pinned on
its own (LOG.md, step 7). Every mechanism the case added (a dollar loan, a
sculpted loan, grace PIK, a sweep, withholding, deferred tax, a promote) is
checked here against its own definition.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict
from one_engine.units import exact

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE = load_yaml_strict((HERE / "nightmare_500mw.yaml").read_text())
OLD = json.loads((HERE / "reference" / "nightmare_old_engine.json").read_text())["ledgers"]
FIXTURE = HERE / "reference" / "nightmare" / "500mw_nightmare.yaml"   # vendored from generic_engine/tests/fixtures
N = 121
Q = F(9125, 36000)                                    # 91.25 / 360
FX = [exact(x) for x in CASE["contracts"]["senior_b"]["terms"]["fx_rate"]]
INDEX = [exact(x) for x in CASE["contracts"]["om_fixed"]["terms"]["index"]]


@pytest.fixture(scope="module")
def r():
    return run_case(copy.deepcopy(CASE))


@pytest.fixture(scope="module")
def old_convention():
    """No escalation and depreciation from the quarter after COD: the old engine's reading."""
    data = copy.deepcopy(CASE)
    for c in data["contracts"].values():
        if c["type"] == "operating_cost":
            c["terms"]["index"] = 1
    data["rulebooks"]["assets"]["first_charge"] = "next_period"
    return run_case(data)


def mv(r, acct, t):
    b = r.group.book("spv")
    return b.balance(acct, t) - b.opening(acct, t)


def bal(r, acct, t):
    return r.group.book("spv").balance(acct, t)


def close(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


def total(r, acct):
    return sum(mv(r, acct, t) for t in range(N))


def paid(r, ref, t):
    return sum((s.paid for s in r.group.settlements if s.ref == ref and s.period == t), F(0))


def unpaid(r, ref):
    return {s.period: s.due - s.paid for s in r.group.settlements if s.ref == ref and s.paid < s.due}


# ── the copy and the integrity ────────────────────────────────────────────

@pytest.mark.skipif(not FIXTURE.exists(), reason="the old engine's fixture is not in this checkout")
def test_the_case_copies_the_fixture():
    import yaml
    fx = yaml.safe_load(FIXTURE.read_text())
    a, fac = fx["financial"]["assumptions"], {f["facility_id"]: f for f in fx["financial"]["facilities"]}
    lines = {x["line_id"]: x for x in fx["revenue_lines"]}
    c = CASE["contracts"]
    for cid, line in (("solar", "solar_ppa"), ("battery", "battery"), ("grid_services", "grid_services")):
        assert [float(v) for v in c[cid]["terms"]["volume"]] == lines[line]["volume"]
        assert {float(c[cid]["terms"]["price"])} == set(lines[line]["price"])
    assert [float(v) for v in FX] == a["fx_spot_rate"] and [float(v) for v in INDEX] == a["opex_escalation_index"]
    sb = fac["senior_b"]
    assert [float(v) for v in c["senior_b"]["terms"]["rate"]] == [b + sb["spread"] for b in sb["base_rate"]]
    for cid in ("senior_b", "mezzanine"):
        assert [float(v) for v in c[cid]["terms"]["draw_schedule"]] == fac[cid]["draw_schedule"]
        assert float(c[cid]["terms"]["arrangement_fee"]) == fac[cid]["arrangement_fee"]
    assert float(c["senior_b"]["terms"]["sculpt_dscr"]) == sb["sculpt_target_dscr"]


def test_integrity_wind_down_and_sources_equal_uses(r):
    assert r.failed_checks() == []
    assert r.group.unpaired_flows() == []
    assert r.rule_checks["construction.sources_equal_uses"] == []
    assert r.rule_checks["reserve_funding.sources_equal_uses"] == []


# ── anchors ───────────────────────────────────────────────────────────────

def test_construction_funding(r):
    assert mv(r, "senior_a.fees", 0) == 180_000_000 * F(5, 1000) * Q                  # 228,125
    assert mv(r, "senior_b.fees", 0) == 90_000_000 * F(75, 10000) * Q * FX[0]          # 155,695.31
    assert mv(r, "senior_b.interest", 1) == 11_250_000 * F(25, 1000) * Q * FX[1]       # 64,902.75, paid in cash
    assert paid(r, "senior_b.draw", 3) == 11_250_000 * FX[3]                           # dollars at the period's rate
    assert paid(r, "shl.draw", 0) == 7_500_000 and paid(r, "senior_a.draw", 0) == 22_500_000
    assert mv(r, "senior_b.deferred_fees", 0) == 900_000 * FX[0] and mv(r, "mezzanine.deferred_fees", 0) == 500_000
    for t in range(8):                                                                 # equity called 11 : 3
        assert mv(r, "lp.capital", t) * 3 == mv(r, "gp.capital", t) * 11
    assert bal(r, "cash", 7) == 0


def test_the_gp_alone_funds_the_dsra_at_cod(r):
    sched = sum(3_000_000 + (180_000_000 - 3_000_000 * (p - 8)) * F(55, 1000) * Q for p in (9, 10))
    assert bal(r, "dsra", 8) == sched == mv(r, "gp.capital", 8)                       # 10,893,281.25
    assert mv(r, "lp.capital", 8) == 0


def test_the_dsra_covers_both_seniors(r):
    """Senior_a's scheduled service plus senior_b's current service (CFADS / 5), two quarters ahead."""
    t = 20
    sched_a = sum(3_000_000 + (180_000_000 - 3_000_000 * (p - 8)) * F(55, 1000) * Q for p in (t + 1, t + 2))
    owed = (bal(r, "senior_b.debt", t) + bal(r, "senior_b.capitalised_interest", t)
            + paid(r, "senior_b.principal", t) + paid(r, "senior_b.capitalised", t))      # before this quarter's payment
    interest = owed * F(55, 1000) * Q
    run_rate = min(owed + interest, max(interest, r.records("waterfall")[t]["cfads"] / 5))
    assert bal(r, "dsra", t) == sched_a + 2 * run_rate


def test_grace_periods_capitalise_interest(r):
    assert mv(r, "senior_b.capitalised_interest", 8) == 90_000_000 * F(55, 1000) * Q * FX[8]
    assert mv(r, "mezzanine.capitalised_interest", 8) == 50_000_000 * F(9, 100) * Q        # 1,140,625
    assert all(mv(r, "senior_b.interest_payable", t) == 0 for t in range(8, 12))
    assert mv(r, "senior_b.capitalised_interest", 12) < 0                                 # repaid from Q12


def test_the_dollar_loan_is_carried_at_the_closing_rate(r):
    for t in range(N):
        assert bal(r, "senior_b.debt_fc", t) * FX[t] == bal(r, "senior_b.debt", t)
        assert bal(r, "senior_b.capitalised_interest_fc", t) * FX[t] == bal(r, "senior_b.capitalised_interest", t)
    assert mv(r, "senior_b.fx_loss", 1) == 11_250_000 * (FX[1] - FX[0])                  # 4,687.50
    assert bal(r, "senior_b.debt_fc", 7) == 90_000_000


def test_operations(r):
    assert mv(r, "solar.revenue", 8) + mv(r, "battery.revenue", 8) + mv(r, "grid_services.revenue", 8) == 12_800_000
    assert mv(r, "om_fixed.expense", 100) == 1_500_000 * INDEX[100]
    assert mv(r, "om_variable.expense", 9) == 2 * exact(CASE["contracts"]["om_variable"]["terms"]["volume"][9]) * INDEX[9]
    assert mv(r, "depreciation", 8) == F(457_000_000, 80)
    assert mv(r, "fixed_assets", 48) == 15_000_000 - mv(r, "depreciation", 48)          # the major maintenance
    assert bal(r, "mmr", 47) == 15_000_000 and bal(r, "mmr", 48) == 0
    assert bal(r, "decommissioning", 87) == 25_000_000 and mv(r, "decommissioning_expense", 88) == 0
    assert bal(r, "decom_reserve", 119) == 25_000_000 and bal(r, "decom_reserve", 120) == 0


def test_senior_b_is_sculpted_to_cfads_over_five(r):
    wf = r.records("waterfall")
    for t in (12, 20, 40):
        service = paid(r, "senior_b.capitalised", t) + paid(r, "senior_b.principal", t) + paid(r, "senior_b.interest", t)
        assert service == wf[t]["cfads"] / 5, t
    # what sculpting leaves is due at maturity; the project cannot pay it at once
    owed_54 = bal(r, "senior_b.debt", 54) + bal(r, "senior_b.capitalised_interest", 54)
    assert owed_54 > 20_000_000 and sorted(unpaid(r, "senior_b.principal")) == [55, 56, 57, 58]
    assert bal(r, "senior_b.debt", 59) == 0


def test_the_mezzanine_sweep_takes_a_quarter_of_what_is_left(r):
    wf = r.records("waterfall")
    swept = [t for t in range(N) if paid(r, "mezzanine.prepay", t) > 0]
    assert swept
    for t in swept[:-1]:                                 # the last sweep is capped by what is owed
        tiers = list(wf[t]["paid"])
        before = sum((wf[t]["paid"][n] for n in tiers[:tiers.index("mezzanine.prepay")]), F(0))
        assert paid(r, "mezzanine.prepay", t) == (wf[t]["available"] - before) / 4, t
    assert sum(paid(r, "mezzanine.prepay", t) for t in swept) == 50_000_000


def test_withholding_is_split_off_and_remitted(r):
    b = r.group.book("spv")
    withheld = {p.event: p.amount for p in b.postings if p.event.endswith(":withheld")}
    for t in (12, 40):
        assert withheld[f"senior_a:claim.interest:{t}:withheld"] == paid(r, "senior_a.interest", t) / 10
    lp_profit = [p for p in b.postings if p.event.startswith("lp:leg.distribute:") and not p.event.endswith(":withheld")]
    assert lp_profit and all(withheld[p.event + ":withheld"] * F(85, 15) == p.amount for p in lp_profit)
    assert not any(e.startswith("lp:leg.return_capital") for e in withheld)                # no WHT on capital
    assert all(bal(r, "wht_payable", t) == 0 for t in range(N))


def test_deferred_tax_is_the_rate_on_the_difference_less_losses(r):
    tax_nbv = lambda t: (max(F(0), 457_000_000 * (1 - F(min(t - 7, 40), 40))) if t >= 8 else F(0)) + \
        (max(F(0), 15_000_000 * (1 - F(min(t - 47, 40), 40))) if t >= 48 else F(0))
    for t in (8, 30, 47, 48, 60, 90, 119):
        losses = r.records("tax")[t]["losses"]
        assert bal(r, "deferred_tax", t) == F(1, 4) * (bal(r, "fixed_assets", t) - tax_nbv(t) - losses), t
    # early, losses carried forward (fast tax depreciation) outweigh the asset difference: a net asset;
    # once they are used, the difference remains: a liability that unwinds as the books catch up
    assert bal(r, "deferred_tax", 40) < 0 < bal(r, "deferred_tax", 100)


def test_over_the_life_tax_is_the_rate_on_book_profit(r):
    scheme = CASE["rulebooks"]["tax"]["scheme"]
    book = sum((total(r, a) * (1 if a.endswith("revenue") else -1)
                for a, cls in scheme.items() if cls in ("taxable", "timing")), F(0))
    assert total(r, "tax_expense") + total(r, "deferred_tax_expense") == book / 4


def test_promote(r):
    pr = r.records("promote")
    wf = r.records("waterfall")
    for t, x in pr.items():                                    # every euro of the residual is allocated
        assert x["roc_lp"] + x["roc_gp"] + x["pref"] + x["catchup"] + x["residual"] == wf[t]["residual"]
    first_gp = min(t for t, x in pr.items() if x["roc_gp"] > 0)
    assert bal(r, "lp.capital", first_gp) == 0                 # the LP's capital comes back first
    pref, catchup = sum(x["pref"] for x in pr.values()), sum(x["catchup"] for x in pr.values())
    assert catchup == pref / 4                                 # caught up to carry / (1 - carry)
    resid = sum(x["residual"] for x in pr.values())
    lp_dist = -sum(mv(r, "lp.distributions", t) for t in range(N))         # gross, withholding included
    assert lp_dist == pref + resid * F(4, 5)


def test_a_promote_without_catch_up_pays_the_gp_its_carry_of_the_residual_only():
    # Averta's HoldCo (step 8) states catchup_pct 0; with a preferred return, that changes the split
    data = copy.deepcopy(CASE)
    data["rulebooks"]["promote"]["catchup_pct"] = 0
    pr = run_case(data).records("promote")
    assert sum(x["pref"] for x in pr.values()) > 0
    assert all(x["catchup"] == 0 for x in pr.values())
    data["rulebooks"]["promote"]["catchup_pct"] = 0.5
    with pytest.raises(InputError, match="catch-up or none"):
        run_case(data)


def test_timing_lines_without_a_tax_register_refuse():
    data = copy.deepcopy(CASE)
    del data["rulebooks"]["tax"]["timing"]
    with pytest.raises(InputError, match="timing"):
        run_case(data)


def test_a_cfads_deduction_that_is_a_claim_refuses():
    data = copy.deepcopy(CASE)
    data["rulebooks"]["waterfall"]["cfads"]["less"] = ["tax", "senior_a.fee"]
    with pytest.raises(InputError, match="account tier"):
        run_case(data)


# ── parity where the old engine is sound ──────────────────────────────────

@pytest.mark.parametrize("acct,ledger,key,kind,sign,periods", [
    ("solar.revenue", "revenue", "solar_ppa_sales", "mv", 1, range(N)),
    ("battery.revenue", "revenue", "battery_sales", "mv", 1, range(N)),
    ("grid_services.revenue", "revenue", "grid_services_sales", "mv", 1, range(N)),
    ("senior_a.debt", "senior_a_debt", "closing", "bal", 1, [t for t in range(N) if t != 8]),
    ("senior_a.interest", "senior_a_interest_expense", "cash_interest", "mv", -1, [t for t in range(N) if t != 9]),
    ("senior_b.interest", "senior_b_interest_expense", "cash_interest", "mv", -1, range(8)),
    ("senior_b.interest", "senior_b_debt", "pik_interest", "mv", 1, range(8, 12)),
    ("senior_b.fx_loss", "fx_gain_loss", "unrealized", "mv", -1, range(12)),
    ("cwip", "cwip", "closing", "bal", 1, range(8)),
    ("decommissioning_expense", "decommissioning_expense", "provision_expense", "mv", -1, range(88)),
])
def test_parity(r, acct, ledger, key, kind, sign, periods):
    f = mv if kind == "mv" else bal
    assert [t for t in periods if not close(f(r, acct, t), sign * OLD[ledger][key][t])] == []


def test_parity_receivables_dollar_loan_and_cod_reserves(r):
    ar = lambda t: sum(bal(r, f"{c}.receivable", t) for c in ("solar", "battery", "grid_services"))
    assert [t for t in range(N) if not close(ar(t), OLD["accounts_receivable"]["closing"][t])] == []
    owed = lambda t: bal(r, "senior_b.debt", t) + bal(r, "senior_b.capitalised_interest", t)
    assert [t for t in range(12) if not close(owed(t), OLD["senior_b_debt"]["closing"][t])] == []
    assert [t for t in range(8) if not close(paid(r, "senior_b.draw", t), OLD["senior_b_debt"]["draw"][t])] == []
    assert close(bal(r, "dsra", 8), OLD["dsra"]["closing"][8])
    assert close(mv(r, "shl.capitalised_interest", 8), OLD["shl_debt"]["pik_interest"][8])
    assert [t for t in range(9, 48) if not close(bal(r, "mmr", t), OLD["maintenance_reserve"]["closing"][t])] == []


def test_parity_under_the_old_conventions(old_convention):
    r = old_convention
    for line in ("om_fixed", "insurance", "land_lease", "grid_charges", "am_fee", "regulatory_levy"):
        assert [t for t in range(N) if not close(mv(r, f"{line}.expense", t), -OLD["opex"][line][t])] == [], line
    assert [t for t in range(48) if not close(mv(r, "depreciation", t),
                                              -OLD["depreciation_expense"]["book_depreciation"][t])] == []
    # the fees line also carries the arrangement fee's amortisation (the fall in deferred fees after Q0)
    commitment = lambda t: mv(r, "senior_b.fees", t) + (mv(r, "senior_b.deferred_fees", t) if t > 0 else 0)
    assert [t for t in range(8) if not close(commitment(t), -OLD["senior_b_interest_expense"]["commitment_fee"][t])] == []


# ── divergences: each a documented old-engine defect, or a finding ────────

def test_cod_quarter_cannot_pay_senior_service_in_full(r):
    """A finding about the fixture, not a defect of either engine: 30-day terms leave 4.2M of
    the first quarter's revenue receivable, so Q8 pays 378k of senior_a principal one quarter
    late. The old engine hides it under 188M of idle cash."""
    assert close(bal(r, "solar.receivable", 8) + bal(r, "battery.receivable", 8) + bal(r, "grid_services.receivable", 8),
                 12_800_000 * 4 * 30 / 365)
    assert list(unpaid(r, "senior_a.principal")) == [8] and paid(r, "senior_a.principal", 9) > 3_000_000
    assert OLD["cash"]["closing"][7] > 188_000_000


def test_costs_escalate_and_variable_om_is_charged(r):
    assert set(OLD["opex"]["om_fixed"][8:]) == {-1_500_000.0}             # the old engine ignores the index
    assert "om_variable" not in OLD["opex"]
    assert total(r, "om_variable.expense") == sum(2 * exact(v) * INDEX[t] for t, v in
                                                  enumerate(CASE["contracts"]["om_variable"]["terms"]["volume"]))


def test_commitment_fees_stop_when_availability_ends(r):
    assert sum(OLD["senior_a_interest_expense"]["commitment_fee"][8:]) < -18_000_000
    assert all(mv(r, "senior_a.fees", t) == 0 for t in range(8, N))


def test_no_overfunding_and_no_idle_cash(r):
    assert close(sum(OLD["cash"]["equity_in"]), 289_976_562.50, 0.01)
    called = sum(max(F(0), mv(r, f"{h}.capital", t)) for h in ("lp", "gp") for t in range(N))
    assert called < 105_000_000
    assert max(bal(r, "cash", t) for t in range(8, N)) == 2_000_000          # the minimum balance, no more


def test_senior_b_is_due_at_maturity_not_after(r):
    assert OLD["senior_b_debt"]["closing"][68] > 0                           # repaid through Q69, past Q55
    fails = {x["period"] for x in r.group.records if x["kind"] == "test" and x["test"] == "dscr_default"
             and x["verdict"] == "fail"}
    assert {55, 56} <= fails


def test_shl_is_paid_in_cash_when_allowed_and_forgiven_not_written_off(r):
    assert close(-sum(OLD["shl_debt"]["terminal_writeoff"]), 164_705_354.49, 0.01)
    forgiven = sum(x["amount"] for x in r.group.records if x["kind"] == "forgiven")
    assert 0 < forgiven < 80_000_000 and mv(r, "shl_forgiven", 120) == forgiven


def test_decommissioning_accrues_its_cost_once(r):
    assert close(sum(OLD["decommissioning_provision"]["provision_accrual"]), 35_000_000)   # 112 quarters, then -10M
    assert total(r, "decommissioning_expense") == 25_000_000


def test_major_maintenance_is_spent_and_capitalised(r):
    assert OLD["cash"]["mmr_release"][48] == 15_000_000                     # released to cash, nothing spent
    assert sum(OLD["fixed_assets"]["cwip_transfer_in"]) == 457_000_000
    assert bal(r, "fixed_assets", 48) - bal(r, "fixed_assets", 47) == 15_000_000 - mv(r, "depreciation", 48)


def test_dividends_are_withheld(r):
    interest_wht = -sum(OLD["cash"]["wht_out"])
    old_interest = -sum(sum(OLD[f"{f}_interest_expense"]["cash_interest"]) for f in ("senior_a", "senior_b", "mezzanine", "shl"))
    assert close(interest_wht, old_interest / 10, 1)                         # the old engine withholds interest only
    dividend_wht = sum(p.amount for p in r.group.book("spv").postings
                       if p.event.endswith(":withheld") and "distribute" in p.event)
    assert dividend_wht > 0
