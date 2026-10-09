"""Averta's 100MW vanilla SPV (plan step 6): anchors, parity, documented divergences.

Referees, in order:

1. analytic anchors computed by hand from the fixture's terms (Averta's
   BUILD_METHODOLOGY_DEBRIEF practice, tolerance 0.01);
2. parity with the old engine (``cases/reference/vanilla_old_engine.json``,
   written by ``consolidation/experiments/make_vanilla_reference.py``)
   wherever the old engine is sound;
3. where it is not, the divergence must be exactly the documented defect,
   and the sponsor's total must reconcile to the old engine's through the
   one line that legitimately differs (tax).
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.contracts import Contract
from one_engine.entities import Group
from one_engine.inputs import InputError, State, Value, load_yaml_strict
from one_engine.packs.contracts import CAPEX, SPONSOR_EQUITY
from one_engine.packs.pf import sources_equal_uses

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE = load_yaml_strict((HERE / "vanilla_100mw.yaml").read_text())
OLD = json.loads((HERE / "reference" / "vanilla_old_engine.json").read_text())
N = 101


def _run(**edits):
    data = copy.deepcopy(CASE)
    if "first_charge" in edits:
        data["rulebooks"]["assets"]["first_charge"] = edits["first_charge"]
    if "price" in edits:
        data["contracts"]["ppa"]["terms"]["price"] = edits["price"]
    return run_case(data)


@pytest.fixture(scope="module")
def base():
    return _run()


@pytest.fixture(scope="module")
def old_convention():
    """The old engine's depreciation convention (first charge the quarter after COD)."""
    return _run(first_charge="next_period")


def mv(r, acct, t):
    b = r.group.book("spv")
    return b.balance(acct, t) - b.opening(acct, t)


def bal(r, acct, t):
    return r.group.book("spv").balance(acct, t)


def close(a, b, tol=0.01):
    return abs(float(a) - float(b)) <= tol


# ── 1. integrity ──────────────────────────────────────────────────────────

def test_integrity_wind_down_and_sources_equal_uses(base):
    assert base.failed_checks() == []
    assert base.group.unpaired_flows() == []
    assert base.rule_checks["funding.sources_equal_uses"] == []


# ── 2. analytic anchors ───────────────────────────────────────────────────

def test_anchors(base):
    assert mv(base, "ppa.revenue", 4) == 62500 * 65                                  # 4,062,500
    assert close(mv(base, "senior.interest", 4), 1_064_583.33)                       # 70M x 6% x 91.25/360
    assert mv(base, "senior.interest", 4) == F(70_000_000) * F(6, 100) * F(9125, 36000)
    assert close(bal(base, "dsra", 4), 5_549_322.92)                                 # next two quarters of senior DS
    assert close(mv(base, "tax_expense", 4), 188_687.50)   # 15% x (4,062,500 - 375,000 - 1,000,000 - 1,064,583.33 - 365,000)
    assert bal(base, "fixed_assets", 4) == 100_000_000 - 1_000_000 and bal(base, "cwip", 4) == 0
    assert sum(mv(base, "equity.capital", t) for t in range(N)) == 4 * (25_000_000 * F(12, 100) + 1_400_000)
    dscr4 = base.records("waterfall")[4]["dscr"]
    assert dscr4 == (F(4_062_500) - 375_000 - F(377_375, 2)) / (1_750_000 + F(3_193_750, 3))
    assert bal(base, "senior.debt", 42) > 0 and bal(base, "senior.debt", 43) == 0
    assert bal(base, "shl.debt", 62) > 0 and bal(base, "shl.debt", 63) == 0


def test_dsra_target_is_the_scheduled_service_of_the_next_two_quarters(base):
    for t in (4, 20, 41, 42, 43):
        sched = []
        for p in (t + 1, t + 2):
            opening = max(0, 70_000_000 - 1_750_000 * (p - 4))
            principal = min(1_750_000, opening)
            sched.append(principal + F(opening) * F(6, 100) * F(9125, 36000))
        assert bal(base, "dsra", t) == sum(sched), t


# ── 3. parity where the old engine is sound ───────────────────────────────

@pytest.mark.parametrize("series,acct,kind", [
    ("revenue", "ppa.revenue", "mv"), ("opex", "om.expense", "mv"),
    ("senior_interest", "senior.interest", "mv"), ("senior_debt", "senior.debt", "bal")])
def test_parity_every_period(base, series, acct, kind):
    f = mv if kind == "mv" else bal
    bad = [t for t in range(N) if not close(f(base, acct, t), OLD[series][t], 0.005)]
    assert bad == []


def test_parity_dsra_from_cod(base):
    # before COD the prefund sits in the DSRA here and in cash in the old engine
    assert [t for t in range(4, N) if not close(bal(base, "dsra", t), OLD["dsra"][t], 0.005)] == []


def test_parity_under_the_old_depreciation_convention(old_convention):
    r = old_convention
    assert [t for t in range(N) if not close(mv(r, "depreciation", t), OLD["depreciation"][t], 0.005)] == []
    assert [t for t in range(5) if not close(mv(r, "tax_expense", t), OLD["tax"][t], 0.005)] == []
    assert close(r.records("waterfall")[4]["dscr"], OLD["dscr"][4], 1e-9)          # 1.1898


# ── 4. divergences are the documented defects, and reconcile ──────────────

def test_construction_is_not_overfunded(base):
    assert sum(OLD["equity_in"]) == 35_600_000 and close(OLD["cash"][3], 23_600_000)
    assert bal(base, "cash", 3) == 0


def test_shareholder_loan_is_repaid_not_written_off(base):
    assert close(sum(OLD["shl_writeoff"]), 7_396_601.51) and OLD["shl_pik"][4] > 0
    assert bal(base, "shl.debt", 63) == 0
    assert all(s.paid == s.due for s in base.group.settlements if s.ref == "shl.interest")


def test_no_spurious_lock_up_near_senior_maturity(base):
    assert OLD["locked_up"][41] and OLD["locked_up"][42] and OLD["dscr"][41] > 1.7
    assert all(x["amount"] == 0 for x in base.records("trapped").values())


def test_depreciation_convention_moves_the_disposal(base, old_convention):
    assert sum(mv(base, "depreciation", t) for t in range(N)) == 97_000_000
    assert mv(base, "disposal_loss", 100) == 3_000_000
    assert mv(old_convention, "disposal_loss", 100) == 4_000_000 == sum(OLD["disposal_loss"])


def test_sponsor_total_reconciles_to_the_old_engine_through_tax(base):
    """Project, senior and reserve flows are identical; so the sponsor's total
    (equity plus SHL) can differ from the old engine's only by the tax difference."""
    new_sponsor = -sum(base.cut("sponsor_flows").values)
    old_sponsor = (-sum(OLD["equity_in"]) + sum(OLD["distribution"]) - 18_000_000
                   + sum(OLD["shl_interest_cash"]) + sum(OLD["shl_repaid"]))
    d_tax = sum(mv(base, "tax_expense", t) for t in range(N)) - sum(OLD["tax"])
    assert close(new_sponsor - old_sponsor, -d_tax)


# ── 5. covenants act ──────────────────────────────────────────────────────

def test_lock_up_traps_cash_pik_the_shl_then_releases():
    r = _run(price=56)
    trapped = {t: x["amount"] for t, x in r.records("trapped").items() if x["amount"]}
    assert sorted(trapped) == [4, 5, 6, 7]
    assert bal(r, "cash", 7) == trapped[7] and bal(r, "cash", 8) == 0                # released at Q8
    pik = [s.period for s in r.group.settlements if s.ref == "shl.interest" and s.paid < s.due]
    assert {4, 5, 6, 7} <= set(pik)
    assert r.failed_checks() == []


def test_default_level_is_read_and_reported():
    r = _run(price=52)
    fails = {x["test"] for x in r.group.records if x["kind"] == "test" and x["verdict"] == "fail"}
    assert "dscr_default" in fails
    assert "dscr_default" not in {x["test"] for x in _run().group.records
                                  if x["kind"] == "test" and x["verdict"] == "fail"}


def test_an_unread_covenant_key_refuses():
    data = copy.deepcopy(CASE)
    data["rulebooks"]["waterfall"]["llcr_lockup"] = 1.1
    with pytest.raises(InputError, match="llcr_lockup"):
        run_case(data)


def test_planted_overfunding_fails_sources_equal_uses():
    g = Group(periods=1, phases=["fund"])
    g.add_contract(Contract("epc", CAPEX, {"owner": "spv"}, {"schedule": Value([100], State.STATED, "t")}, "USD"))
    eq = Contract("equity", SPONSOR_EQUITY, {"issuer": "spv"}, {}, "USD")
    g.add_contract(eq)
    g.register("fund", lambda g, t: g.post_leg(eq, "contribute", 130, t, "fund"))   # 30 too much
    g.run()
    assert sources_equal_uses(g, "spv", "cash", "fund") == [0]
