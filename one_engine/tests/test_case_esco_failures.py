"""Reproduction test: esco-product's two failure-world oracles, Bulb Energy and Indiana Toll Road, run on one_engine.

Where the numbers come from
---------------------------
esco-product (``engine/tests/test_m4.py``) reproduces two published failures
as pure configuration of its own engine:

* **Bulb** (blind brief orc-c05): a fixed-tariff supplier meets an unhedged
  wholesale shock; the published band for the accumulated deficit is GBP
  2.0-2.58bn, and the state's levy on all bills recovers exactly that.
* **Indiana Toll Road v2** (orc-c31v2): an accreting interest-only bullet
  meets falling traffic; first missed payment June 2014 (~102.5), debt at
  default ~3,132, recovery ~88.9c on the dollar, equity wiped, the state's
  +3,800 never reverses.

The translations are ``cases/panel/bulb.yaml`` and
``cases/panel/indiana_toll_road.yaml``. They are held to two referees:

1. **esco-product's own engine, posting for posting.** Each fixture was run
   through esco-product's engine (commit ``48a319e``) and every account's
   closing balance in every period was captured exactly
   (``cases/reference/esco_engine/*.json``, hash-pinned with its origin;
   written by ``consolidation/experiments/capture_esco_fixture.py``). Here
   every one of those balances must be equal, as a fraction. That is the
   parity the switch-over needs: esco-product can call one_engine instead of
   its own engine and read the same books.
2. **The published figures**, with esco-product's own tolerances: every
   assertion of ``test_m4.py`` on these two fixtures is repeated below.

Reading esco-product's books
----------------------------
esco-product posts every movement payer -> payee, each account moving by the
sign of its leg. one_engine posts debit and credit by account nature. Party
positions are asset accounts, debts and claims liabilities, the sponsors'
equity equity, so their balances read the same. Two accounts carry the
other sign: the concession asset (esco-product recognises the debt and equity
*out of* it) and interest expense (esco-product recognises a claim out of it
and discharges a paid claim back into it). They are turned where read
(``SIGN``).

If this fails, the engine or the configuration is wrong, never the number.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.oracles import verify
from one_engine.tests.test_chain import journal_is_ledger

CASES = pathlib.Path(__file__).resolve().parents[1] / "cases"
REFERENCE = CASES / "reference"
BULB = CASES / "panel" / "bulb.yaml"
ITR = CASES / "panel" / "indiana_toll_road.yaml"
SIGN = {"concession_asset": -1, "interest_expense": -1}


def green(res):
    assert res.failed_checks() == []
    for entity, book in res.group.books.items():
        assert journal_is_ledger(book), entity


def esco_ledger(fixture):
    rel = f"esco_engine/{fixture}.json"
    verify(rel)                                         # the stored answer is the one captured
    return json.loads((REFERENCE / rel).read_text())


@pytest.fixture(scope="module")
def bulb():
    res = run_case(BULB)
    green(res)
    return res.group.books["deal"]


@pytest.fixture(scope="module")
def itr():
    res = run_case(ITR)
    green(res)
    return res


def flow(book, acct, t):
    return book.balance(acct, t) - book.opening(acct, t)


# ── 1. esco-product's own engine, every account, every period ───────────────

@pytest.mark.parametrize("case,fixture", [(BULB, "bulb"), (ITR, "indiana-toll-road")])
def test_every_balance_equals_esco_products_engine(case, fixture):
    book = run_case(case).group.books["deal"]
    ref = esco_ledger(fixture)
    assert book.periods == ref["periods"]
    assert set(ref["closing"]) <= set(book.accounts)
    for acct, values in ref["closing"].items():
        assert [SIGN.get(acct, 1) * book.balance(acct, t) for t in range(ref["periods"])] == \
            [F(v) for v in values], acct


# ── 2. Bulb: the published band and mechanism (test_m4.py) ──────────────────

def test_bulb_accumulated_deficit_in_the_published_band(bulb):
    deficit = -bulb.balance("energy_bill_payers", 23)          # the levy is the deficit
    assert 2.0e9 <= deficit <= 2.58e9
    assert float(deficit) == pytest.approx(2.29e9, abs=0.005e9)  # the prior independent run's -2.29bn


def test_bulb_shock_path_and_seasonality(bulb):
    flows = [flow(bulb, "supplier_trading", t) for t in range(24)]
    assert abs(float(flows[0])) < 0.05e6            # anchor month: margin about nil
    assert flows[1] < 0                              # first deficit Feb 2021
    assert flows.index(min(flows)) == 12             # worst month Jan 2022
    assert float(flows[12]) == pytest.approx(-225.46e6, abs=0.2e6)


def test_bulb_state_finances_the_book_transfers_the_levy_socialises(bulb):
    assert bulb.balance("supplier_trading", 23) == 0            # book transferred, loss left
    assert bulb.balance("state_admin_fund", 23) == 0            # the levy recovers it fully
    assert float(-bulb.balance("customers_billbase", 23)) == pytest.approx(3.825e9, abs=1e4)
    assert float(bulb.balance("wholesale_market", 23)) == pytest.approx(3.1498e9, abs=1e5)
    total = sum((bulb.balance(a, 23) for a in bulb.accounts), F(0))
    assert total == 0                                            # one pocket each; nothing created


# ── 3. Indiana Toll Road: the published anchors (test_m4.py) ────────────────

def test_itr_first_missed_payment_june_2014(itr):
    book = itr.group.books["deal"]
    assert book.balance("senior_debt", 14) == 3030
    assert flow(book, "senior_lenders", 15) == 0                 # nothing paid
    missed = book.balance("senior_debt", 15) - 3030
    assert missed == F(10246551, 100000)
    assert float(missed) == pytest.approx(102.5, abs=0.155 * 102.5)
    rec = itr.records("bullet")
    assert [t for t in range(17) if rec[t]["defaulted"]] == list(range(15, 17))


def test_itr_debt_at_default_and_capitalisation_at_the_accreting_charge(itr):
    book = itr.group.books["deal"]
    d15, d16 = book.balance("senior_debt", 15), book.balance("senior_debt", 16)
    assert d15 == 3030 + F(10246551, 100000)
    assert abs(float(d15) - 3132.0) <= 0.15 * 3132.0
    assert float(d16 - d15) == pytest.approx(float(d15) * 0.035038, abs=1e-3)
    assert flow(book, "senior_lenders", 16) == 0


def test_itr_sale_recovery_and_shortfall(itr):
    book = itr.group.books["deal"]
    assert float(-book.balance("buyer_2015", 17)) == pytest.approx(3800.0 * 0.893 / 1.22, abs=0.01)
    dist = flow(book, "senior_lenders", 17)
    face = dist + book.balance("senior_debt", 17) + book.balance("interest_claim", 17)
    assert 0.839 <= float(dist / face) <= 0.939
    assert float(face) == pytest.approx(3359.78, abs=0.05)
    assert book.balance("spv_cash", 17) == 0                     # equity wiped
    assert float(book.balance("senior_debt", 17)) == pytest.approx(372.63, abs=0.05)
    assert float(book.balance("senior_lenders", 17)) == pytest.approx(1050.35, abs=0.05)


def test_itr_the_states_payment_never_reverses_and_cash_never_goes_negative(itr):
    book = itr.group.books["deal"]
    for t in range(18):
        assert book.balance("state_cash", t) == 3800, t
        assert book.balance("spv_cash", t) >= 0, t


# ── the serviced bullet's own rules ──────────────────────────────────────────

def _bullet(income):
    """esco-product's unit bullet (test_m4.py BULLET_DEAL): 100 at 10% a year for 3 years, income each year."""
    return {
        "case": "unit-bullet", "periods": 3, "phases": ["open", "accrue", "settle", "close"], "currency": "LCU",
        "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "opening": {"deal": {a: {"nature": n, "value": 0} for a, n in [
            ("spv", "asset"), ("lender", "asset"), ("world", "asset"), ("funding", "asset"),
            ("debt", "liability"), ("claim", "liability"), ("expense", "expense")]}},
        "rulebooks": {
            "setup": {"kind": "transfer", "entity": "deal", "phase": "accrue", "moves": {
                "draw": {"debit": "funding", "credit": "debt", "amount": [100, 0, 0]},
                "income": {"debit": "spv", "credit": "world", "amount": income}}},
            "interest": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {"i": {
                "claim": "claim", "against": "expense", "share": F(1, 10), "of": {"account": "debt", "read": "balance"}}}},
            "bullet": {"kind": "serviced_bullet", "entity": "deal", "phase": "settle", "cash": "spv",
                       "lender": "lender", "debt": "debt", "claim": "claim", "expense": "expense",
                       "funding": "funding", "maturity": 2}},
    }


@pytest.mark.parametrize("income,lender_flows,debt_closings,spv_final", [
    ([60, 60, 60], [10, 10, 110], [100, 100, 0], 50),          # served; bullet repaid in full
    ([6, 6, 6], [0, 0, 18], [110, 121, F(1151, 10)], 0),        # default at t0, compounding
])
def test_bullet_pays_capitalises_and_sweeps_as_esco_product(income, lender_flows, debt_closings, spv_final):
    res = run_case(_bullet(income))
    green(res)
    book = res.group.books["deal"]
    assert [flow(book, "lender", t) for t in range(3)] == lender_flows
    assert [book.balance("debt", t) for t in range(3)] == debt_closings
    assert book.balance("spv", 2) == spv_final
    assert all(book.balance("claim", t) == 0 for t in range(3))  # the claim settles every period


def test_a_bullet_refuses_a_maturity_outside_its_window():
    from one_engine.inputs import InputError
    doc = _bullet([60, 60, 60])
    doc["rulebooks"]["bullet"]["maturity"] = 3
    with pytest.raises(InputError, match="maturity 3 is not inside"):
        run_case(doc)
