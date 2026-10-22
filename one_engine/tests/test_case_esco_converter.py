"""Reproduction test: every deal esco-product's case converter builds, run on one_engine, closes every account as esco-product's engine does.

Where the numbers come from
---------------------------
esco-product turns each LOD 0 case file (who promises what to whom, which
figure sizes which promise) into a deal config for its own engine
(``oe.engine.from_case.to_spec``), runs it, and reads the ledger back as its
dated cash-flow table per contract leg (R2a, ``engine/out/r2a``) and the
route and party views built on it. The switch-over replaces the engine under
that converter, so the parity it needs is on the converter's output: for
every case that books legs, every achievement scenario and every shortfall
rule, the same deal config must close every account at the same balance.

``consolidation/experiments/capture_esco_cases.py`` ran all of them through
esco-product's engine (commit ``48a319e``): 18 runs over 12 cases (the
eleven single-run cases and fi-compass's three achievement levels under
three shortfall rules: no recovery, recovery in the same period, recovery
two periods later). Each run's deal config and every account's closing
balance in every period are stored in ``cases/reference/esco_engine/
converter_runs.json`` (hash-pinned).

The converter's deal configs use four of esco-product's operators. Each
maps onto one_engine as follows (``translate`` below):

* ``op1`` fixed fee: the notional paid from payer to payee in each of its
  ``terms`` periods (a ``transfer``). A fee leg the converter books as a
  claim (``<payer>_obligations`` -> ``<promise>_claim``) is the claim's
  recognition. A ``sale_at_npv`` settlement is the buyer paying the seller
  the stream's present value at the discount rate, at period ``at``;
  the price is computed here, exactly as esco-product's operator does
  (the sum of each later payment over (1 + rate) to its distance from
  ``at``). At the switch-over the converter states it.
* ``op2`` sharing (split fraction 1, the only form the converter writes):
  the measured savings x achievement, capped at the measured savings,
  paid from the avoided bill to the payee (a ``transfer``).
* ``op3`` ranked distribution (direct assignee, pro rata ties, no reserve:
  the converter's form) is one_engine's ``ranked_distribution`` rulebook.
* ``op9`` metric reduction posts nothing.

Reading esco-product's books: esco-product posts payer -> payee, each
account moving by the sign of its leg. Here the payee is debited and the
payer credited, so an asset or expense reads the same and a liability,
equity or income account the other way round. The natures chosen keep the
reading the same everywhere except the avoided bill (``savings_resource``,
income: what the savings were paid out of), which is turned where read.

Not exercised by any case today, so not translated: the pledged-account
routing of ``op3`` and its reserve targets, ``op2`` splits with a
counterparty or a guarantee floor. A config using one fails the translation
loudly, not silently.

If this fails, the engine or the translation is wrong, never the number.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.oracles import verify

REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"
REL = "esco_engine/converter_runs.json"
WORKS, SAVINGS = "works", "savings_resource"
SIGN = {SAVINGS: -1}


def amount(x):
    """A deal-config amount as esco-product's loader books it: floats at 6 decimals, fractions as written."""
    return F(str(round(x, 6))) if isinstance(x, float) else F(str(x))


def nature(acct):
    if acct == WORKS:
        return "expense"
    if acct == SAVINGS:
        return "income"
    if acct.endswith("_obligations"):
        return "equity"
    if acct.endswith("_claim"):
        return "liability"
    return "asset"                                     # party cash and a payer's collections pool


def translate(doc):
    """esco-product's converter output (an op1/op2/op3/op9 deal config) as a one_engine case."""
    n = doc["horizon"]
    accounts = doc["settings"]["op8_ledger"]["accounts"]
    moves, settle_moves, rulebooks = {}, {}, {}

    def series(start, terms, x):
        return [x if start <= t < start + terms else 0 for t in range(n)]

    for k, inst in enumerate(doc["instruments"]):
        op = inst["operator"]
        sched = inst.get("schedule_ref", {})
        start, phase = sched.get("start", 0), sched.get("phase")
        if op == "op1_instrument_generation":
            assert inst["instrument_type"] == "fixed_fee" and phase == "accrue", inst
            terms, notional = inst.get("terms", sched.get("terms")), amount(inst["notional"])
            payer, payee = inst["payer"], inst["payee"]
            # the payee is debited and the payer credited (module docstring); a claim is a liability, so its
            # recognition credits it against the payer's obligations (equity) debited: the same reading
            dr, cr = (payer, payee) if nature(payee) in ("liability",) else (payee, payer)
            moves[f"i{k}"] = {"debit": dr, "credit": cr, "amount": series(start, terms, notional)}
            st = inst.get("settlement")
            if st:
                assert st["kind"] == "sale_at_npv" and st.get("phase", "settle") == "settle", st
                d, at = amount(st["discount_rate"]), st["at"]
                price = sum((notional / (1 + d) ** (start + i - at) for i in range(terms) if start + i > at), F(0))
                settle_moves[f"i{k}_sale"] = {"debit": st["seller"], "credit": st["buyer"],
                                              "amount": [price if t == at else 0 for t in range(n)]}
        elif op == "op2_sharing_rules":
            assert phase == "settle" and amount(inst["split_fraction"]) == 1 and not inst.get("guarantee_floor"), inst
            q, perf = amount(inst["measured_input"]), amount(inst.get("performance", 1))
            settle_moves[f"i{k}"] = {"debit": inst["payee"], "credit": inst["source"],
                                     "amount": series(start, sched["terms"], min(perf * q, q))}
        elif op == "op3_ranked_distribution":
            assert inst.get("priority_interactions", "direct_assignee") == "direct_assignee", inst
            assert not inst.get("reserve_targets") and inst.get("tie_rule", "pro_rata") == "pro_rata", inst
            rec = inst.get("shortfall_recovery", {"exercised": True, "lag": 0})
            rule = "none" if not rec["exercised"] else ("absorb" if rec.get("lag", 0) == 0 else "deferred")
            payers = {acct for i2 in doc["instruments"] if i2["operator"] == "op1_instrument_generation"
                      for acct in [i2["payer"]] if acct.endswith("_obligations")
                      and i2["payee"] in {lv["claim"] for lv in inst["level_order"]}}
            assert len(payers) == 1, payers
            rulebooks[f"i{k}"] = {
                "kind": "ranked_distribution", "entity": "deal", "phase": "settle",
                "collect_from": inst["collect_from"], "pool": inst["available_from"], "claims_book": payers.pop(),
                "from": start, "until": start + sched["terms"] - 1, "performance": amount(inst.get("performance", 1)),
                "shortfall": rule, "lag": rec.get("lag", 0) if rule == "deferred" else 0,
                "levels": [{"claim": lv["claim"], "to": lv["payee"], "kind": lv["kind"]} for lv in inst["level_order"]]}
        else:
            assert op == "op9_metric_reduction", op
    return {
        "case": doc["deal"], "periods": n, "phases": ["open", "accrue", "settle", "close"], "currency": "LCU",
        "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "opening": {"deal": {a: {"nature": nature(a), "value": 0} for a in accounts}},
        # esco-product's order within a period: generation (op1, op2) before reduction (op3)
        "rulebooks": {"accrue": {"kind": "transfer", "entity": "deal", "phase": "accrue", "moves": moves},
                      "settle": {"kind": "transfer", "entity": "deal", "phase": "settle", "moves": settle_moves},
                      **rulebooks},
    }


def runs():
    verify(REL)
    return json.loads((REFERENCE / REL).read_text())["runs"]


RUNS = runs()


@pytest.mark.parametrize("label", sorted(RUNS))
def test_every_balance_equals_esco_products_engine(label):
    ref = RUNS[label]
    res = run_case(translate(ref["deal"]))
    assert res.failed_checks() == []
    book = res.group.books["deal"]
    assert book.periods == ref["periods"]
    for acct, values in ref["closing"].items():
        assert [SIGN.get(acct, 1) * book.balance(acct, t) for t in range(ref["periods"])] == \
            [F(v) for v in values], acct


def test_the_runs_cover_every_shortfall_rule_and_a_sale():
    rules, sales = set(), 0
    for ref in RUNS.values():
        case = translate(ref["deal"])
        rules |= {rb["shortfall"] for rb in case["rulebooks"].values() if rb["kind"] == "ranked_distribution"}
        sales += any(k.endswith("_sale") for k in case["rulebooks"]["settle"]["moves"])
    assert rules == {"none", "absorb", "deferred"} and sales == 9 and len(RUNS) == 18


# ── the rulebook's own rules ─────────────────────────────────────────────────

def _waterfall(rule, lag=0, performance=F(1, 2)):
    """One payer owing a fixed claim of 60 and a variable claim of 40 a period for two periods; 100 of cash."""
    natures = {"cash": "asset", "pool": "asset", "owes": "equity", "fixed_claim": "liability",
               "var_claim": "liability", "senior": "asset", "junior": "asset", "world": "income"}
    return {
        "case": "unit-ranked", "periods": 4, "phases": ["accrue", "settle"], "currency": "LCU",
        "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "opening": {"deal": {a: {"nature": nv, "value": 0} for a, nv in natures.items()}},
        "rulebooks": {
            "accrue": {"kind": "transfer", "entity": "deal", "phase": "accrue", "moves": {
                "income": {"debit": "cash", "credit": "world", "amount": [100, 0, 0, 0]},
                "fixed": {"debit": "owes", "credit": "fixed_claim", "amount": [60, 60, 0, 0]},
                "var": {"debit": "owes", "credit": "var_claim", "amount": [40, 40, 0, 0]}}},
            "wf": {"kind": "ranked_distribution", "entity": "deal", "phase": "settle", "collect_from": "cash",
                   "pool": "pool", "claims_book": "owes", "until": 1, "performance": performance,
                   "shortfall": rule, "lag": lag,
                   "levels": [{"claim": "fixed_claim", "to": "senior", "kind": "fixed"},
                              {"claim": "var_claim", "to": "junior", "kind": "variable"}]}},
    }


def flow(book, acct, t):
    return book.balance(acct, t) - book.opening(acct, t)


@pytest.mark.parametrize("rule,lag,senior,junior", [
    # half of 100 is collected each period: 50 against a fixed 60
    ("absorb", 0, [60, 60, 0, 0], [-10, -10, 0, 0]),       # the junior pays the 10 into the pool, the same period
    ("none", 0, [50, 50, 0, 0], [0, 0, 0, 0]),             # the senior bears it
    ("deferred", 2, [50, 50, 10, 10], [0, 0, -10, -10]),   # the junior pays it to the senior two periods later
])
def test_a_fixed_shortfall_is_absorbed_borne_or_recovered(rule, lag, senior, junior):
    res = run_case(_waterfall(rule, lag))
    assert res.failed_checks() == []
    book = res.group.books["deal"]
    assert [flow(book, "senior", t) for t in range(4)] == senior
    assert [flow(book, "junior", t) for t in range(4)] == junior
    assert all(book.balance(c, t) == 0 for c in ("fixed_claim", "var_claim", "owes") for t in range(4))
    assert [res.records("wf")[t]["deficit"] for t in range(2)] == ([0, 0] if rule == "absorb" else [10, 10])


def test_a_surplus_is_shared_pro_rata_and_capped_at_each_claim():
    res = run_case(_waterfall("none", performance=F(9, 10)))   # 90 against 60 fixed: the junior gets 30 of 40
    book = res.group.books["deal"]
    assert [flow(book, "junior", t) for t in range(2)] == [30, 30]
    assert book.balance("pool", 1) == 0
    res = run_case(_waterfall("none", performance=F(6, 5)))    # 120 collected: the junior is capped at its 40
    book = res.group.books["deal"]
    assert [flow(book, "junior", t) for t in range(2)] == [40, 40]
    assert book.balance("pool", 1) == 40                        # what the caps leave stays in the pool


def test_a_lag_belongs_to_a_deferred_recovery_only():
    from one_engine.inputs import InputError
    for rule, lag in (("deferred", 0), ("none", 1), ("absorb", 2)):
        with pytest.raises(InputError, match="only it has a lag"):
            run_case(_waterfall(rule, lag))
