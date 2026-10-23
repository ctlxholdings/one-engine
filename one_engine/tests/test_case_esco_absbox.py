"""Reproduction test: every vehicle esco-product runs on AbsBox/Hastructure 0.52.3, run on one_engine, pays what that engine paid.

Where the numbers come from
---------------------------
esco-product sends its securitisation vehicles to Hastructure 0.52.3 through
the AbsBox client (``oe.pool.engine_client``): the ficompass example bought
directly or through a warehouse (``run_matrix``, three achievement scenarios
under three cover rules each), the renesco/LaBEEF pool of six bought one
pool at a time with draws on a loan and an equity tranche (``pool_run``), and
the NYSERDA 2020A green bonds under seven scenarios, from the printed
schedule to CPR and default stresses with the Authority's clean-up call
(``issue_runner``). ``consolidation/experiments/capture_esco_pool_specs.py``
replaced the engine client with a recorder and stored each vehicle as the
runner handed it over, beside the output esco-product recorded for that run.
They are in ``cases/reference/esco_engine/absbox_runs.json`` (hash-pinned).

Each run goes through ``run_case`` as one ``vehicle`` rulebook
(:mod:`one_engine.packs.securitisation`): the spec, assets, pool assumption,
revolving pools and draws exactly as captured, on a grid of calendar years.
Translation is only this: a warehouse buys from a ConstantAsset revolving
pool and the pool of six from StaticAsset pools, one per leg, as
esco-product's engine client sets them up (its A3 workaround), and the pool
of six's draws land in ``acc01``, the account its client names.

What is compared, to the cent
-----------------------------
* run_matrix and pool_run: the loan's interest paid, its last principal
  date and end balance, what each note received, what the equity tranche
  took out, the purchase price paid, the pool's collections.
* NYSERDA: every bond's interest and principal on every payment date, no
  payment on any other date, every account's balance at the end (the
  closing sinks included), the pool's collections, the administrative fee
  paid, the bank interest earned (reinvestment) and the Authority's call
  money.

If this fails, the engine or the translation is wrong, never the number.
"""
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError
from one_engine.oracles import verify
from one_engine.packs.securitisation import Vehicle, VehicleError

REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"
REL = "esco_engine/absbox_runs.json"


def runs():
    verify(REL)
    return {r["id"]: r for r in json.loads((REFERENCE / REL).read_text())["runs"]}


RUNS = runs()


def inputs(ref):
    """The rulebook's inputs for one captured run (module docstring: the translation)."""
    if ref["runner"] == "run_matrix":
        if "warehouse" in ref["id"]:
            return {"revolving": {"Consol": {"kind": "ConstantAsset", "assets": [ref["assets"]]}}}
        return {"assets": [ref["assets"]]}
    if ref["runner"] == "pool_run":
        return {"revolving": {k: {"kind": "StaticAsset", "assets": [a]} for k, a in ref["assets"].items()},
                "fundings": [{"date": d, "bond": b, "account": "acc01", "amount": x} for d, b, x in ref["extra"]]}
    return {"assets": ref["assets"], **({"pool_assumption": ref["extra"]} if ref["extra"] else {})}


def case(spec, **more):
    """One vehicle on a grid of calendar years from its first date to its stated maturity."""
    d = spec["dates"]
    first = dt.date.fromisoformat(d.get("cutoff") or d["lastCollect"]).year
    ends = [f"{y}-12-31" for y in range(first, dt.date.fromisoformat(d["stated"]).year + 1)]
    return {
        "case": spec["name"], "periods": len(ends), "phases": ["settle"], "currency": "LCU",
        "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "rulebooks": {"vehicle": {"kind": "vehicle", "entity": "spv", "phase": "settle", "spec": spec,
                                  "period_ends": ends, **more}},
    }


def run(ref):
    res = run_case(case(ref["spec"], **inputs(ref)))
    assert res.failed_checks() == []
    recs = res.records("vehicle")
    pay = [p for t in sorted(recs) for p in recs[t]["payments"]]
    return res, recs, pay


def paid(pay, bond, kinds=("int", "prin", "yield"), date=None):
    return sum((p[3] for p in pay if p[1] == bond and p[2] in kinds and (date is None or p[0] == date)), F(0))


def total(recs, key):
    return sum((x for r in recs.values() for x in r[key].values()), F(0))


def cents(x):
    return F(str(x))


NOTES = sorted(k for k, r in RUNS.items() if r["runner"] != "issue_runner")
NYSERDA = sorted(k for k, r in RUNS.items() if r["runner"] == "issue_runner")


@pytest.mark.parametrize("rid", NOTES)
def test_every_note_is_paid_what_esco_products_engine_paid(rid):
    ref = RUNS[rid]
    res, recs, pay = run(ref)
    rec = ref["recorded"]
    cash = rec.get("cash") or rec["engine_cash"]
    book = res.group.books["spv"]
    last = book.periods - 1
    assert paid(pay, "Loan", ("int",)) == cents(rec["loan_interest"])
    assert max(p[0] for p in pay if p[1] == "Loan" and p[2] == "prin") == rec["loan_last_pay"]
    assert book.balance("bond.Loan", last) == cents(rec["loan_end_balance"])
    assert paid(pay, "Equity", ("prin", "yield")) == cents(rec["equity_out"])
    assert {n: paid(pay, n) for n in ("Loan", "Equity")} == {n: cents(x) for n, x in cash["paid_to_notes"].items()}
    assert sum((p[2] for r in recs.values() for p in r["purchases"]), F(0)) == cents(cash["purchase_paid"])
    assert total(recs, "collections") == cents(rec.get("pool_collected", cash.get("pool_collected")))


@pytest.mark.parametrize("rid", NYSERDA)
def test_every_nyserda_bond_payment_equals_esco_products_engine(rid):
    ref = RUNS[rid]
    res, recs, pay = run(ref)
    rec = ref["recorded"]
    book = res.group.books["spv"]
    last = book.periods - 1
    expected = set()
    for pb in rec["per_bond"]:
        for p in pb["payments"]:
            expected.add((pb["bond"], p["date"]))
            assert (paid(pay, pb["bond"], ("int",), p["date"]), paid(pay, pb["bond"], ("prin",), p["date"])) == \
                (cents(p["interest"]), cents(p["principal"])), (pb["bond"], p["date"])
        assert book.balance(f"bond.{pb['bond']}", last) == 0
    assert {(p[1], p[0]) for p in pay} == expected
    assert {a: book.balance(f"acc.{a}", last) for a in rec["cash"]["accounts_end"]} == \
        {a: cents(x) for a, x in rec["cash"]["accounts_end"].items()}
    assert total(recs, "collections") == cents(rec["pool_collected"])
    assert total(recs, "fees_paid") == cents(rec["cash"]["admin_paid"])
    assert total(recs, "bank_interest") == cents(rec["cash"]["reinvestment"])
    assert total(recs, "liquidity") == cents(rec["cash"]["authority_call_money"])
    assert sum((p[3] for p in pay), F(0)) == cents(rec["cash"]["paid_to_bonds"])


def test_the_runs_cover_every_vehicle_esco_product_sends():
    kinds = [r["runner"] for r in RUNS.values()]
    assert (kinds.count("run_matrix"), kinds.count("pool_run"), kinds.count("issue_runner")) == (18, 1, 7)
    assert sum("warehouse" in k for k in NOTES) == 9
    called = [k for k in NYSERDA if RUNS[k]["spec"].get("liquidity")]
    stressed = [k for k in NYSERDA if RUNS[k]["extra"]]
    assert len(called) == 3 and len(stressed) == 6


def test_the_book_balances_a_held_pool_against_the_bonds_at_the_open():
    ref = RUNS[NYSERDA[0]]
    res = run_case(case(ref["spec"], **inputs(ref)))
    book = res.group.books["spv"]
    # NYSERDA 2020A closes with 16.69m of bonds against 16.69m of proceeds: the held pool is the residual
    assert book.opening("acc.proceeds", 0) == book.opening("bond.T2036", 0) + 13450000
    assert book.opening("opening_equity", 0) == book.opening("pool.PoolConsol", 0)


# ── the interpreter's own rules ──────────────────────────────────────────────

def _deal(waterfall, accounts=(("cash", {"balance": 1000.0}),), bonds=None, **more):
    spec = {"name": "unit", "dates": {"cutoff": "2024-01-01", "closing": "2024-01-01", "firstPay": "2024-02-01",
                                      "payFreq": ["DayOfMonth", 1], "poolFreq": "MonthEnd", "stated": "2024-06-30"},
            "accounts": list(accounts),
            "bonds": bonds or [["A", {"balance": 600.0, "rate": 0.0, "startDate": "2024-01-01",
                                      "bondType": {"Sequential": None}, "lastAccrueDate": "2024-01-01"}],
                               ["B", {"balance": 400.0, "rate": 0.0, "startDate": "2024-01-01",
                                      "bondType": {"Sequential": None}, "lastAccrueDate": "2024-01-01"}]],
            "waterfall": {"amortizing": waterfall}, "collection": [], "status": ["PreClosing", "Amortizing"], **more}
    return spec


def test_a_pro_rata_payment_floors_each_share_and_gives_the_rounding_to_the_first():
    spec = _deal([["If", ["date", "in", "2024-02-01"], ["payPrin", "cash", ["A", "B"], {"limit": {"formula": ["const", 0.05]}}]]])
    v = Vehicle(spec).run()
    # 0.05 split 600:400 is 0.03 and 0.02; 0.01 split is 0.006 and 0.004, floored to 0.00 and 0.00, rounding 0.01 to A
    assert [(x[0], x[2]) for _, k, x in v.dated if k == "payment"] == [("A", F(3, 100)), ("B", F(2, 100))]
    spec = _deal([["If", ["date", "in", "2024-02-01"], ["payPrin", "cash", ["A", "B"], {"limit": {"formula": ["const", 0.01]}}]]])
    assert [(x[0], x[2]) for _, k, x in Vehicle(spec).run().dated if k == "payment"] == [("A", F(1, 100))]


def test_a_liquidity_draw_leaves_the_providers_credit_as_it_was():
    # Hastructure 0.52.3's CreditEnhancement.draw keeps the old credit: two calls of 300 on a credit of 300 both pay
    spec = _deal([["liqSupport", "lp", "account", ["cash"], {"formula": ["const", 500.0]}]],
                 liquidity={"lp": {"type": {"total": 300.0}, "start": "2024-01-01", "credit": 300.0}})
    v = Vehicle(spec).run()
    assert [x for _, k, x in v.dated if k == "liquidity"][:2] == [["lp", 300], ["lp", 300]]


def test_bank_interest_weighs_each_balance_by_its_own_floored_day_count():
    spec = _deal([["If", ["date", "in", "2024-02-01"], ["transfer", "cash", "out", {"formula": ["const", 500.0]}]]],
                 accounts=(("cash", {"balance": 1001.0, "interest": {"period": "MonthEnd", "rate": 1.0,
                                                                      "lastSettleDate": "2024-01-01"}}),
                           ("out", {"balance": 0})))
    v = Vehicle(spec).run()
    earned = [x for d, k, x in v.dated if k == "bank_interest"]
    # January: no statement line yet, (1001 x 1.0) x 29/360 = 80.636, floored 80.63. February: 1081.63 for
    # 01-31..02-01 (30E: 1/360) = 3.0045, floored 3.00; then 581.63 to 02-29 (28/360) = 45.2379, floored 45.23:
    # 48.23, where one floor over the month would give 48.24
    assert earned[:2] == [["cash", F(8063, 100)], ["cash", F(4823, 100)]]


MORTGAGE = ["Mortgage", {"originBalance": 1200.0, "originRate": ["fix", 0.06], "originTerm": 12, "freq": "Monthly",
                         "type": "Level", "originDate": "2024-01-15"},
            {"currentBalance": 1200.0, "currentRate": 0.06, "remainTerm": 12, "status": "Current"}]


def test_a_default_takes_the_whole_balance_once_the_defaults_still_expected_cover_it():
    # 1200 to default in two halves: in the first period the 600 due plus the 600 still expected cover the
    # balance, so Hastructure defaults all of it at once (projCashflowByDefaultAmt)
    spec = _deal([], collection=[["CollectedInterest", "cash"], ["CollectedPrincipal", "cash"],
                                 ["CollectedPrepayment", "cash"]])
    spec["dates"]["stated"] = "2025-06-30"
    assumption = ["ByIndex", [[0], [["Mortgage", {"ByAmount": [1200.0, [0.5, 0.5]]}, {"CPR": 0.0}, None, None], None, None]]]
    v = Vehicle(spec, [MORTGAGE], assumption).run()
    assert [(str(d), x[1]) for d, k, x in v.dated if k == "default"] == [("2024-02-29", 1200)]


def test_a_static_pool_sells_at_most_its_value_and_keeps_the_cash_unspent():
    spec = _deal([["If", ["date", "in", "2024-02-01"], ["buyAssetFromTo", ["Current|Defaulted", 1.0, 0], "cash", None, "rp", "p1"]]],
                 accounts=(("cash", {"balance": 1500.0}),), bonds=[], collection=[["CollectedInterest", "cash"],
                                                                                  ["CollectedPrincipal", "cash"]])
    v = Vehicle(spec, revolving={"rp": {"kind": "StaticAsset", "assets": [MORTGAGE]}})
    v.run()
    # valued at 1200 x 1.0: a constant pool would take all 1500, a static one 1200, all of it, and nothing is left
    assert [x for _, k, x in v.dated if k == "purchase"] == [["p1", 1200, 1200]]
    assert v.revolving["rp"][1][0].cb == 0


def test_a_pool_amount_no_collection_rule_takes_refuses():
    spec = _deal([], collection=[["CollectedPrincipal", "cash"]])
    spec["dates"]["stated"] = "2025-06-30"
    with pytest.raises(InputError, match="no collection rule collects"):
        Vehicle(spec, [MORTGAGE]).run()


def test_an_overdraft_and_an_unbuilt_term_refuse_by_name():
    spec = _deal([["payFee", "cash", ["admin"]]], fees=[["admin", {"type": {"fixFee": 10.0}, "feeStart": "2024-01-01"}]])
    with pytest.raises(InputError, match="fee type .* is not built"):
        Vehicle(spec)
    spec = _deal([["calcInt", "A"], ["payResidual", "cash", "A"]])
    with pytest.raises(InputError, match="waterfall action 'payResidual' is not built"):
        Vehicle(spec).run()
    v = Vehicle(_deal([]))
    with pytest.raises(VehicleError, match="draw 2000 from cash"):
        v._draw("cash", dt.date(2024, 2, 1), F(2000), "x", "test")


def test_period_ends_must_cover_the_run():
    ref = RUNS[NYSERDA[0]]
    data = case(ref["spec"], **inputs(ref))
    data["rulebooks"]["vehicle"]["period_ends"] = data["rulebooks"]["vehicle"]["period_ends"][:-6]
    data["periods"] -= 6
    with pytest.raises(InputError, match="after the last period end"):
        run_case(data)
