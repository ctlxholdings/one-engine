"""The 50MW feature-coverage SPV (plan step 7): anchors, parity, documented divergences.

Same referees as the 100MW case (tests/test_case_vanilla.py): hand-computed
anchors, parity with the stored old-engine run where it is sound
(``cases/reference/feature_old_engine.json``), and an exact reconciliation
of the sponsor's total through the lines that legitimately differ.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict
from one_engine.kernel import KernelError

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE = load_yaml_strict((HERE / "feature_50mw.yaml").read_text())
OLD = json.loads((HERE / "reference" / "feature_old_engine.json").read_text())["ledgers"]
N = 41


@pytest.fixture(scope="module")
def r():
    return run_case(copy.deepcopy(CASE))


def mv(r, acct, t):
    b = r.group.book("spv")
    return b.balance(acct, t) - b.opening(acct, t)


def bal(r, acct, t):
    return r.group.book("spv").balance(acct, t)


def close(a, b, tol=0.005):
    return abs(float(a) - float(b)) <= tol


def total(r, acct):
    return sum(mv(r, acct, t) for t in range(N))


# ── integrity ─────────────────────────────────────────────────────────────

def test_integrity_wind_down_and_sources_equal_uses(r):
    assert r.failed_checks() == []
    assert r.group.unpaired_flows() == []
    assert r.rule_checks["funding.sources_equal_uses"] == []


# ── anchors ───────────────────────────────────────────────────────────────

def test_anchors(r):
    q = F(9125, 36000)                                                   # 91.25 / 360
    assert mv(r, "senior.fees", 0) == F(35_000_000) * F(5, 1000) * q     # 44,357.64 on the undrawn commitment
    assert mv(r, "senior.interest", 1) == F(8_750_000) * F(5, 100) * q   # 110,894.10, construction interest
    assert bal(r, "ppa.receivable", 4) == F(10_000_000) * 30 / 365       # 821,917.81
    assert bal(r, "vat_receivable", 3) == F(2, 10) * (4 * (12_500_000 + 250_000) + 500_000)   # 10.3M of capex VAT
    assert mv(r, "dsra.fee", 4) == F(15, 1000) * F(9125, 36500) * (     # LC fee on next two quarters of DS
        2 * F(35_000_000, 36) + (35_000_000 - F(35_000_000, 36)) * F(5, 100) * q
        + (35_000_000 - 2 * F(35_000_000, 36)) * F(5, 100) * q)
    assert bal(r, "fixed_assets", 4) == 51_500_000 - F(51_500_000, 40)
    assert bal(r, "decommissioning", 39) == 36 * 50_000 and mv(r, "decommissioning", 40) == -1_800_000
    assert bal(r, "senior.debt", 38) > 0 and bal(r, "senior.debt", 39) == 0


def test_funding_is_uses_less_draws_with_construction_interest_as_a_use(r):
    f0 = r.records("funding")[0]
    uses = 12_500_000 + 250_000 + 500_000 + F(2, 10) * 13_250_000 + F(35_000_000) * F(5, 1000) * F(9125, 36000)
    assert sum(f0["uses"].values()) == uses
    assert f0["residual"] == uses - 8_750_000 - 1_250_000                 # 5,944,357.64


def test_minimum_cash_is_a_stock_not_a_flow(r):
    assert all(bal(r, "cash", t) >= 500_000 for t in range(4, 40))
    assert all(bal(r, "cash", t) == 500_000 for t in range(5, 40))       # nothing else idles
    assert bal(r, "cash", 40) == 0


# ── parity where the old engine is sound ──────────────────────────────────

@pytest.mark.parametrize("acct,ledger,key,kind,sign", [
    ("ppa.revenue", "revenue", "solar_ppa_sales", "mv", 1),
    ("ppa.receivable", "accounts_receivable", "closing", "bal", 1),
    ("om_fixed.expense", "opex", "om_fixed", "mv", -1),
    ("insurance.expense", "opex", "insurance", "mv", -1),
    ("land_lease.expense", "opex", "land_lease", "mv", -1),
    ("senior.interest", "senior_interest_expense", "cash_interest", "mv", -1),
    ("senior.debt", "senior_debt", "closing", "bal", 1),
    ("dsra.fee", "lc_fee_expense", "lc_fee", "mv", -1),
    ("decommissioning_expense", "decommissioning_expense", "provision_expense", "mv", -1),
    ("decommissioning", "decommissioning_provision", "closing", "bal", 1),
])
def test_parity_every_period(r, acct, ledger, key, kind, sign):
    f = mv if kind == "mv" else bal
    assert [t for t in range(N) if not close(f(r, acct, t), sign * OLD[ledger][key][t])] == []


def test_parity_construction_cost_and_commitment_fee(r):
    cwip = [OLD["cwip"][k][t] for k in ("epc_capex", "development_costs", "fees_capitalized") for t in range(4)]
    assert close(sum(mv(r, "cwip", t) for t in range(4)), sum(cwip))
    assert [t for t in range(4) if not close(mv(r, "senior.fees", t), -OLD["senior_interest_expense"]["commitment_fee"][t])] == []


def test_parity_under_the_old_depreciation_convention():
    data = copy.deepcopy(CASE)
    data["rulebooks"]["assets"]["first_charge"] = "next_period"
    old_conv = run_case(data)
    assert [t for t in range(N)
            if not close(mv(old_conv, "depreciation", t), -OLD["depreciation_expense"]["book_depreciation"][t])] == []
    # Q4 tax differs by exactly 25% of the two lines the old engine leaves out of the base
    gap = mv(old_conv, "om_variable.expense", 4) + mv(old_conv, "decommissioning_expense", 4)
    assert close(mv(old_conv, "tax_expense", 4), -OLD["tax_expense"]["current_tax"][4] - float(gap) / 4)


# ── divergences: each a documented old-engine defect ──────────────────────

def test_variable_om_is_charged(r):
    assert "om_variable" not in OLD["opex"]                               # the old engine never reads it
    assert total(r, "om_variable.expense") == 2 * 25_000 * 37


def test_commitment_fee_stops_when_availability_ends(r):
    assert sum(OLD["senior_interest_expense"]["commitment_fee"][4:]) < -800_000   # charged on repaid principal
    assert all(mv(r, "senior.fees", t) == 0 for t in range(4, N))


def test_construction_is_not_overfunded(r):
    assert close(sum(OLD["cash"]["equity_in"]), 27_576_258.68, 0.01) and close(OLD["cash"]["closing"][3], 5_000_000)
    assert bal(r, "cash", 3) == 0


def test_cash_is_not_hoarded(r):
    assert OLD["cash"]["closing"][39] > 31_000_000                        # min cash deducted every quarter
    assert max(bal(r, "cash", t) for t in range(5, N)) == 500_000


def test_unpaid_bullet_shl_is_forgiven_as_a_gain(r):
    assert close(-sum(OLD["shl_debt"]["terminal_writeoff"]), 5_094_745.63, 0.01)
    forgiven = [x for x in r.group.records if x["kind"] == "forgiven"]
    assert [x["account"] for x in forgiven] == ["shl.debt"] and mv(r, "shl_forgiven", 40) == forgiven[0]["amount"] > 0


def test_sponsor_total_reconciles_through_the_three_differences(r):
    """Sponsor (equity + SHL) total, new minus old, equals: minus the variable O&M
    the old engine drops, plus the commitment fee it overcharges, minus the tax
    difference. Opex VAT nets to zero over the life; nothing else differs."""
    new = -sum(r.cut("sponsor_flows").values)
    c = OLD["cash"]
    old = (-sum(c["equity_in"]) - sum(c["distribution_out"]) - 5_000_000
           - sum(OLD["shl_interest_expense"]["cash_interest"]) - sum(OLD["shl_debt"]["scheduled_repayment"]))
    fee_overcharge = -sum(OLD["senior_interest_expense"]["commitment_fee"]) - total(r, "senior.fees")
    d_tax = total(r, "tax_expense") + sum(OLD["tax_expense"]["current_tax"])
    assert close(new - old, -total(r, "om_variable.expense") + fee_overcharge - d_tax, 1e-6)


def test_forgiveness_posted_as_a_loss_refuses():
    data = copy.deepcopy(CASE)
    data["rulebooks"]["shl_forgiveness"]["gain"] = "depreciation"        # an expense account: a 'loss'
    with pytest.raises(KernelError):
        run_case(data)
