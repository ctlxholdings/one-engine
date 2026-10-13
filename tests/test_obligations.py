"""The obligations pack (:mod:`one_engine.packs.obligations`), piece by piece.

Each rulebook is run on a small hand-built case whose every number can be
checked by eye. The failure panel (``test_case_esco_panel.py``) runs the
same pieces on the panel's deals; this file pins each rule on its own,
including the ones the panel never exercises (a put whose claim stays
outstanding, a par tier ranked behind an unpaid one, a planted posting that
breaks a pool's composition).
"""
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError
from one_engine.packs.obligations import ObligationError


def case(rulebooks, accounts, periods=3, generators=None):
    """A one-book case ("deal") with the given accounts (name -> nature), all opening at nil."""
    data = {"case": "obligations-unit", "periods": periods, "phases": ["accrue", "settle"],
            "currency": "LCU", "floors": {},
            "opening": {"deal": {a: {"nature": n, "value": 0} for a, n in accounts.items()}},
            "contracts": {}, "rulebooks": rulebooks, "schemes": {}, "cuts": {}, "measures": {}}
    if generators:
        data["generators"] = generators
    return data


def bal(res, acct, t):
    return res.group.book("deal").balance(acct, t)


def flow(res, acct, t):
    b = res.group.book("deal")
    return b.balance(acct, t) - b.opening(acct, t)


POCKETS = {"payer": "asset", "pool": "asset", "payee": "asset", "junior": "asset",
           "senior_claim": "liability", "junior_claim": "liability", "claims_book": "equity",
           "written_off": "income", "costs": "expense"}


# ── duty ──────────────────────────────────────────────────────────────────

def test_a_duty_performs_while_its_condition_holds_and_is_suspended_otherwise():
    res = run_case(case({"route": {"kind": "duty", "entity": "deal", "phase": "accrue",
                                   "condition": [True, False, True],
                                   "perform": {"debit": "pool", "credit": "payer", "amount": 10}}}, POCKETS))
    assert [flow(res, "pool", t) for t in range(3)] == [10, 0, 10]
    recs = res.records("route")
    assert [recs[t]["status"] for t in range(3)] == ["operative", "suspended", "operative"]
    assert [recs[t]["satisfied"] for t in range(3)] == [True, False, True]
    assert res.failed_checks() == []


def test_a_duty_with_no_performance_is_a_pure_determination():
    res = run_case(case({"servicing": {"kind": "duty", "entity": "deal", "phase": "accrue",
                                       "condition": [True, True, False]}}, POCKETS))
    assert not res.group.book("deal").postings
    assert [res.records("servicing")[t]["status"] for t in range(3)] == ["operative", "operative", "suspended"]


def test_a_duty_runs_only_inside_its_window():
    res = run_case(case({"route": {"kind": "duty", "entity": "deal", "phase": "accrue", "condition": True,
                                   "from": 1, "until": 1,
                                   "perform": {"debit": "pool", "credit": "payer", "amount": 10}}}, POCKETS))
    assert [flow(res, "pool", t) for t in range(3)] == [0, 10, 0]
    assert set(res.records("route")) == {1}


# ── prohibition ───────────────────────────────────────────────────────────

def test_a_prohibition_pays_its_remedy_only_when_violated():
    res = run_case(case({"guard": {"kind": "prohibition", "entity": "deal", "phase": "accrue",
                                   "forbidden": [False, True, False],
                                   "remedy": {"debit": "costs", "credit": "payer", "amount": 7}}}, POCKETS))
    assert [flow(res, "costs", t) for t in range(3)] == [0, 7, 0]
    recs = res.records("guard")
    assert [recs[t]["violated"] for t in range(3)] == [False, True, False]
    assert recs[1]["remedy_paid"] == 7 and recs[0]["remedy_paid"] == 0


# ── claim ─────────────────────────────────────────────────────────────────

def test_a_claim_measured_on_a_flow_takes_its_share_times_the_index_at_fire_time():
    res = run_case(case({
        "route": {"kind": "duty", "entity": "deal", "phase": "accrue", "condition": True,
                  "perform": {"debit": "pool", "credit": "payer", "amount": 100}},
        "claims": {"kind": "claim", "entity": "deal", "phase": "settle", "claims": {
            "senior": {"claim": "senior_claim", "against": "claims_book", "share": F("0.6"), "index": [1, 1, F("0.5")],
                       "of": {"account": "pool", "read": "flow"}}}}}, POCKETS))
    # the claim is never paid here, so it accumulates: 60, 60, then 30 at the halved index
    assert [bal(res, "senior_claim", t) for t in range(3)] == [60, 120, 150]
    assert res.records("claims")[2]["claims"] == {"senior": 30}


def test_a_stated_claim_is_recognised_in_its_window_and_a_triggered_one_once():
    res = run_case(case({"claims": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {
        "fixed": {"claim": "senior_claim", "against": "claims_book", "amount": 5, "from": 1, "until": 2},
        "crystallised": {"claim": "junior_claim", "against": "claims_book", "amount": 9,
                         "crystallizes_on": [False, True, True]}}}}, POCKETS))
    assert [flow(res, "senior_claim", t) for t in range(3)] == [0, 5, 5]
    assert [flow(res, "junior_claim", t) for t in range(3)] == [0, 9, 0]


def test_a_claim_refuses_a_negative_amount_and_two_timings():
    with pytest.raises(ObligationError, match="negative"):
        run_case(case({"claims": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {
            "x": {"claim": "senior_claim", "against": "claims_book", "amount": -1}}}}, POCKETS))
    with pytest.raises(InputError, match="either"):
        run_case(case({"claims": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {
            "x": {"claim": "senior_claim", "against": "claims_book", "amount": 1, "crystallizes_on": True, "from": 0}}}},
            POCKETS))


# ── put ───────────────────────────────────────────────────────────────────

def test_a_put_pays_par_of_the_outstanding_claim_once_at_the_first_trigger():
    res = run_case(case({
        "claims": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {
            "debt": {"claim": "senior_claim", "against": "claims_book", "amount": 40, "from": 0, "until": 1}}},
        "put": {"kind": "put", "entity": "deal", "phase": "settle", "trigger": [False, True, True],
                "guarantor": "payer", "pool": "pool", "claim": "senior_claim"}}, POCKETS))
    # par at the trigger is the claim outstanding then (40 + 40); nothing settles it here, yet the
    # put is not exercised again in period 2
    assert [flow(res, "pool", t) for t in range(3)] == [0, 80, 0]
    assert res.records("put") == {1: {"kind": "put", "period": 1, "exercised": True, "par": 80}}


# ── stack ─────────────────────────────────────────────────────────────────

def _stack(tiers, extra=None, pool_in=100, senior=60, junior=60, accounts=POCKETS):
    rb = {
        "fund": {"kind": "transfer", "entity": "deal", "phase": "accrue",
                 "moves": {"in": {"debit": "pool", "credit": "payer", "amount": pool_in}}},
        "claims": {"kind": "claim", "entity": "deal", "phase": "accrue", "claims": {
            "senior": {"claim": "senior_claim", "against": "claims_book", "amount": senior, "from": 0, "until": 0},
            "junior": {"claim": "junior_claim", "against": "claims_book", "amount": junior, "from": 0,
                       "until": 0}}},
        "stack": {"kind": "stack", "entity": "deal", "phase": "settle", "pool": "pool",
                  "claims_book": "claims_book", "tiers": tiers, **(extra or {})}}
    return run_case(case(rb, accounts, periods=1))


def test_a_stack_pays_in_rank_and_carries_what_it_cannot_pay():
    res = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "carry"},
                  {"name": "junior", "claim": "junior_claim", "to": "junior", "shortfall": "carry"}])
    assert (flow(res, "payee", 0), flow(res, "junior", 0)) == (60, 40)
    assert (bal(res, "senior_claim", 0), bal(res, "junior_claim", 0)) == (0, 20)     # arrears
    assert bal(res, "pool", 0) == 0 and res.failed_checks() == []
    labels = [p.event for p in res.group.book("deal").postings if p.phase == "settle"]
    assert labels.index("stack.senior.paid:0") < labels.index("stack.junior.paid:0")


def test_a_stack_extinguishes_what_it_cannot_pay_when_the_tier_says_so():
    res = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "extinguish"},
                  {"name": "junior", "claim": "junior_claim", "to": "junior", "shortfall": "extinguish"}],
                 extra={"extinguished": "written_off"}, pool_in=50)
    assert (flow(res, "payee", 0), flow(res, "junior", 0)) == (50, 0)
    assert (bal(res, "senior_claim", 0), bal(res, "junior_claim", 0)) == (0, 0)
    assert bal(res, "written_off", 0) == 10 + 60                                       # the creditors' loss
    assert res.records("stack")[0]["extinguished"] == {"senior": 10, "junior": 60}


def test_par_tiers_are_paid_whatever_the_pool_and_the_residual_holder_funds_the_gap():
    res = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "call"},
                  {"name": "junior", "claim": "junior_claim", "to": "costs", "shortfall": "call"}],
                 extra={"residual": "junior"}, pool_in=100)
    assert (flow(res, "payee", 0), flow(res, "costs", 0)) == (60, 60)
    assert flow(res, "junior", 0) == -20 and bal(res, "pool", 0) == 0                  # the cash call
    assert res.records("stack")[0]["call"] == 20


def test_a_par_tier_behind_an_unpaid_one_calls_enough_to_pay_both():
    # sequential ranks: the call that pays the par tier in full must pay everything ahead of it first
    res = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "carry"},
                  {"name": "junior", "claim": "junior_claim", "to": "costs", "shortfall": "call"}],
                 extra={"residual": "junior"}, pool_in=30)
    assert (flow(res, "payee", 0), flow(res, "costs", 0), flow(res, "junior", 0)) == (60, 60, -90)


def test_a_positive_residual_goes_to_its_holder_or_stays_in_the_pool():
    held = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "call"}],
                  extra={"residual": "junior"}, pool_in=100, junior=0)
    assert flow(held, "junior", 0) == 40 and bal(held, "pool", 0) == 0
    left = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "carry"}],
                  pool_in=100, junior=0)
    assert bal(left, "pool", 0) == 40


def test_a_stack_sweeps_its_sources_into_the_pool_first():
    accounts = {**POCKETS, "estate": "asset"}
    res = _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "carry"}],
                 extra={"pool": "estate", "sweep": ["pool"]}, pool_in=100, junior=0, accounts=accounts)
    assert bal(res, "pool", 0) == 0 and flow(res, "payee", 0) == 60 and bal(res, "estate", 0) == 40


def test_a_stack_refuses_what_it_cannot_do():
    with pytest.raises(InputError, match="residual"):
        _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "call"}])
    with pytest.raises(InputError, match="extinguished"):
        _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "extinguish"}])
    with pytest.raises(InputError, match="shortfall"):
        _stack([{"name": "senior", "claim": "senior_claim", "to": "payee", "shortfall": "maybe"}])


# ── pool ──────────────────────────────────────────────────────────────────

POOL_ACCOUNTS = {"pool": "asset", "m1": "income", "m2": "income", "m3": "income", "payer": "asset"}


def _pool(entries, thresholds=(), members=None, extra_rulebooks=None, periods=4):
    members = members or {m: {"quantity": 10, "performance": 1, "account": m} for m in ("m1", "m2", "m3")}
    rb = {"pool": {"kind": "pool", "entity": "deal", "phase": "accrue", "pool_to": "pool",
                   "members": members, "entries": entries, "thresholds": list(thresholds)}}
    rb.update(extra_rulebooks or {})
    return run_case(case(rb, POOL_ACCOUNTS, periods=periods))


def test_members_stream_into_the_pool_for_their_terms_on_their_own_performance_path():
    res = _pool([{"member": "m1", "entry": 0, "terms": 4}, {"member": "m2", "entry": 1, "terms": 2}],
                members={"m1": {"quantity": 10, "performance": [1, 1, 0.5, 0], "account": "m1"},
                         "m2": {"quantity": 4, "performance": 1, "account": "m2"},
                         "m3": {"quantity": 1, "performance": 1, "account": "m3"}})
    assert [flow(res, "pool", t) for t in range(4)] == [10, 14, 9, 0]
    assert res.records("pool")[1]["entered"] == ["m2"] and res.failed_checks() == []


def test_an_origination_floor_blocks_an_entry_that_reads_a_demand_below_it_the_period_before():
    floor = {"id": "viability", "kind": "origination_floor", "measure": [100, 60, 40, 40], "floor": 55,
             "if_missing": "pass"}
    res = _pool([{"member": "m1", "entry": 0, "terms": 4}, {"member": "m2", "entry": 2, "terms": 2},
                 {"member": "m3", "entry": 3, "terms": 1}], thresholds=[floor])
    recs = res.records("pool")
    assert recs[0]["entered"] == ["m1"]                 # nothing to read before the first period: the rule
    assert recs[2]["entered"] == ["m2"]                 # reads 60 at period 1
    assert recs[3]["skipped"] == {"m3": ["viability"]}  # reads 40 at period 2
    assert bal(res, "m3", 3) == 0


def test_an_origination_floor_needs_a_rule_for_a_missing_reading():
    floor = {"id": "viability", "kind": "origination_floor", "measure": [100, 60, 40, 40], "floor": 55}
    with pytest.raises(Exception, match="missing"):
        _pool([{"member": "m1", "entry": 0, "terms": 4}], thresholds=[floor])


def test_the_pool_composition_check_fails_on_a_posting_to_a_member_from_outside_the_pool():
    planted = {"stray": {"kind": "transfer", "entity": "deal", "phase": "settle",
                         "moves": {"x": {"debit": "payer", "credit": "m1", "amount": 1}}}}
    res = _pool([{"member": "m1", "entry": 0, "terms": 4}], extra_rulebooks=planted)
    assert any("pool.composition" in f for f in res.failed_checks())


def test_a_pool_refuses_an_unknown_member_and_a_second_stint():
    with pytest.raises(InputError, match="unknown member"):
        _pool([{"member": "m9", "entry": 0, "terms": 1}])
    with pytest.raises(InputError, match="twice"):
        _pool([{"member": "m1", "entry": 0, "terms": 1}, {"member": "m1", "entry": 2, "terms": 1}])
