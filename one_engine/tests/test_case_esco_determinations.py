"""Reproduction test: esco-product's three determination oracles (ORNL 2018, ISDA Hellenic 2012, Lomas), run on one_engine.

Where the numbers come from
---------------------------
esco-product (``engine/tests/test_m3.py``) reproduces three published rulings
as configuration of its own engine, each first reproduced blind by an
earlier engineer:

* **ORNL 2018**: 167 guaranteed-savings projects, an honest annual M&V
  verification gating settlement, guarantees struck a buffer beta below
  expected performance. Expected: the portfolio's realisation (verified over
  guaranteed) about 1/(1 - beta) = 111.1%, the share of projects at or above
  their guarantee about Phi(beta/sigma) = 81.3%; with no buffer, 100% and a
  coin-flip 50%. (DOE's published 108.0% and 90.4% give the direction; the
  buffer is the mechanism.) The ensemble is a seeded generator on the test
  side, as in esco-product: same seed, same draws, same order.
* **ISDA Hellenic 2012** (``cases/panel/hellenic_2012.yaml``): the
  Restructuring Credit Event definition over the Greek facts. Expected: YES,
  all three limbs true; leaving one fact out flips it only on the principal
  reduction, the approval and the binding force.
* **Lomas** (``cases/panel/lomas.yaml``): a payment whose condition precedent
  is "no continuing default of the other party". Expected: SUSPENDED every
  period; a cure revives it; only termination extinguishes it, for good; the
  payer's own default never excuses it; the payee's demand changes nothing.

Referees: esco-product's own engine for ORNL (its two ensemble statistics
were captured exactly, ``cases/reference/esco_engine/ornl_2018.json``, and
must come out equal to the last bit), and esco-product's assertions, with
its tolerances, for all three.

One difference in reading facts, deliberate: esco-product's determinations
are closed-world (a fact left out is false). one_engine's formulas refuse a
name that is not stated, so "leave a fact out" is "set it to 0" here, and a
fact a case forgot refuses instead of silently counting as false.

If this fails, the engine or the configuration is wrong, never the number.
"""
import copy
import json
import math
import pathlib
import random

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict
from one_engine.oracles import verify
from one_engine.tests.test_chain import journal_is_ledger

CASES = pathlib.Path(__file__).resolve().parents[1] / "cases"
HELLENIC = CASES / "panel" / "hellenic_2012.yaml"
LOMAS = CASES / "panel" / "lomas.yaml"


def green(res):
    assert res.failed_checks() == []
    for entity, book in res.group.books.items():
        assert journal_is_ledger(book), entity


def flow(res, acct, t):
    b = res.group.books["deal"]
    return b.balance(acct, t) - b.opening(acct, t)


# ── ORNL 2018: realisation under verification-gated settlement ──────────────

N_PROJECTS, SEED = 167, 2018
EXPECTED_SAVINGS, SIGMA_PERFORMANCE, SIGMA_VERIFICATION = 0.44, 0.10, 0.05


def ornl_case(b0, performance, verification):
    """The portfolio over ``cycles`` annual verification snapshots, one case: each project's verified savings
    (baseline x performance x verification, the M&V gate) post in accrue and are paid in full to the provider
    in settle (a split of 1). ``performance`` and ``verification`` are per project, one value per cycle."""
    cycles = len(performance[0])
    inputs, values, gate, settle = {"split": 1}, {}, {}, {}
    for i in range(len(b0)):
        inputs.update({f"b0_{i}": b0[i], f"perf_{i}": performance[i], f"ver_{i}": verification[i]})
        values[f"verified_{i}"] = f"b0_{i} * perf_{i} * ver_{i}"
        values[f"paid_{i}"] = f"split * verified_{i}"
        gate[f"p{i}"] = {"debit": "verified_savings", "credit": "measured_world",
                         "amount": {"generator": "portfolio", "output": f"verified_{i}"}}
        settle[f"p{i}"] = {"debit": "provider_cash", "credit": "verified_savings",
                           "amount": {"generator": "portfolio", "output": f"paid_{i}"}}
    return {
        "case": "ornl-2018-portfolio", "periods": cycles, "phases": ["open", "accrue", "settle", "close"],
        "currency": "LCU", "floors": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "generators": {"portfolio": {"kind": "formulas", "periods": cycles, "inputs": inputs, "values": values}},
        "opening": {"deal": {"measured_world": {"nature": "income", "value": 0},
                             "verified_savings": {"nature": "asset", "value": 0},
                             "provider_cash": {"nature": "asset", "value": 0}}},
        "rulebooks": {"mv_gate": {"kind": "transfer", "entity": "deal", "phase": "accrue", "moves": gate},
                      "settlement": {"kind": "transfer", "entity": "deal", "phase": "settle", "moves": settle}},
    }


def ensemble(cycles, buffer, seed=SEED):
    """esco-product's ``_ensemble``, draw for draw, with the engine run on one_engine: (realisation, share)."""
    rng = random.Random(seed)
    b0 = [rng.lognormvariate(math.log(100000.0), 0.5) for _ in range(N_PROJECTS)]
    guarantee = [EXPECTED_SAVINGS * (1 - buffer) * b for b in b0]
    perf = [[0.0] * cycles for _ in range(N_PROJECTS)]
    ver = [[0.0] * cycles for _ in range(N_PROJECTS)]
    for c in range(cycles):
        for i in range(N_PROJECTS):
            perf[i][c] = round(EXPECTED_SAVINGS * (1 + rng.gauss(0.0, SIGMA_PERFORMANCE)), 6)
            ver[i][c] = round(1 + rng.gauss(0.0, SIGMA_VERIFICATION), 6)
    res = run_case(ornl_case([round(b, 6) for b in b0], perf, ver))
    green(res)
    book = res.group.books["deal"]
    paid = {(p.event, p.period): p.amount for p in book.postings if p.phase == "settle"}
    g_sum = sum(guarantee)
    realization_sum, at_or_above, n = 0.0, 0, 0
    for c in range(cycles):
        assert book.balance("verified_savings", c) == 0           # the settlement drains what the gate stocked
        v_sum = 0.0
        for i in range(N_PROJECTS):
            v = float(paid.get((f"settlement.p{i}:{c}", c), 0))
            v_sum += v
            at_or_above += v >= guarantee[i]
            n += 1
        realization_sum += v_sum / g_sum
    return realization_sum / cycles, at_or_above / n


@pytest.fixture(scope="module")
def esco_ornl():
    verify("esco_engine/ornl_2018.json")
    return json.loads((CASES / "reference" / "esco_engine" / "ornl_2018.json").read_text())


@pytest.fixture(scope="module")
def or18():
    return ensemble(48, 0.10)


def test_or18_equals_esco_products_engine_to_the_last_bit(or18, esco_ornl):
    ref = esco_ornl["buffered"]
    assert (ref["cycles"], ref["buffer"], ref["seed"], ref["projects"]) == (48, 0.1, SEED, N_PROJECTS)
    assert or18 == (float(ref["realization"]), float(ref["share_at_or_above"]))


def test_or18_realisation_rate_oracle(or18):
    realization, _ = or18
    assert realization * 100 == pytest.approx(111.1, abs=0.8)
    assert realization == pytest.approx(1 / (1 - 0.10), abs=0.008)


def test_or18_share_at_or_above_guarantee_oracle(or18):
    _, share = or18
    assert share * 100 == pytest.approx(81.3, abs=1.5)
    sigma = math.sqrt(SIGMA_PERFORMANCE ** 2 + SIGMA_VERIFICATION ** 2)
    assert share == pytest.approx(0.5 * (1 + math.erf((0.10 / sigma) / math.sqrt(2))), abs=0.025)


def test_or18_with_no_buffer_the_elevation_disappears(esco_ornl):
    """The mechanism, not the parameter: no buffer gives 100% and a coin-flip 50%."""
    realization, share = ensemble(16, 0.0)
    ref = esco_ornl["no_buffer"]
    assert (realization, share) == (float(ref["realization"]), float(ref["share_at_or_above"]))
    assert realization * 100 == pytest.approx(100.0, abs=0.6)
    assert share * 100 == pytest.approx(50.0, abs=2.5)


def test_or18_the_gate_shuts_off_unverified_savings():
    res = run_case(ornl_case([100000.0], [[0.44]], [[0.0]]))
    green(res)
    assert res.group.books["deal"].postings == ()                 # nothing verified: nothing books


# ── ISDA Hellenic 2012: the credit-event determination ──────────────────────

def hellenic(**facts):
    doc = load_yaml_strict(HELLENIC.read_text())
    for k, v in facts.items():
        assert k in doc["generators"]["facts"]["inputs"], k
        doc["generators"]["facts"]["inputs"][k] = v
    res = run_case(doc)
    green(res)
    return res


def test_hel12_determination_yes_with_three_limbs():
    res = hellenic()
    assert res.group.books["deal"].postings == ()                 # a pure determination posts nothing
    assert res.records("named_event")[0]["status"] == "operative"  # the event occurs: YES
    g = res.generated["facts"]
    assert (g["reduction_or_deferral"], g["direction_or_consent"], g["binds_holders"]) == (1, 1, 1)


# the published leave-one-out table: fact -> does the event still occur
LOO_TABLE = {
    "government_enacted_cac_after_issuance": True,   # background: still YES
    "principal_reduction_fraction": False,           # limb (a) loses its only support: NO
    "action_launched_by_government": True,           # (b) keeps direction and consent
    "supermajority_approved": False,                 # limb (c) exercise gone: NO
    "supermajority_acceptance_binds_all": False,     # limb (c) force gone: NO
    "government_directed_action": True,
    "government_consented_to_action": True,
}


@pytest.mark.parametrize("fact", LOO_TABLE)
def test_hel12_leave_one_out_flips_exactly_where_published(fact):
    res = hellenic(**{fact: 0})
    occurs = res.records("named_event")[0]["status"] == "operative"
    assert occurs is LOO_TABLE[fact], fact
    if fact == "principal_reduction_fraction":
        assert res.generated["facts"]["reduction_or_deferral"] == 0


def test_hel12_both_government_attribution_facts_out_still_yes():
    res = hellenic(government_directed_action=0, government_consented_to_action=0)
    assert res.generated["facts"]["direction_or_consent"] == 1
    assert res.records("named_event")[0]["status"] == "operative"


# ── Lomas: the condition-precedent suspension ───────────────────────────────

def lomas(**facts):
    doc = load_yaml_strict(LOMAS.read_text())
    for k, v in facts.items():
        assert k in doc["generators"]["facts"]["inputs"], k
        doc["generators"]["facts"]["inputs"][k] = v
    res = run_case(doc)
    green(res)
    return res


def statuses(res):
    return [res.records("payment_duty")[t]["status"] for t in range(4)]


def test_lomas_suspended_every_period_and_nothing_posts():
    res = lomas()
    assert statuses(res) == ["suspended"] * 4
    assert res.group.books["deal"].postings == ()


def test_lomas_cure_revives_the_duty():
    res = lomas(counterparty_default_continuing=[1, 1, 0, 0])
    assert statuses(res) == ["suspended", "suspended", "operative", "operative"]
    assert [flow(res, "payee_cash", t) for t in range(4)] == [0, 0, 1000, 1000]
    assert res.group.books["deal"].balance("payee_cash", 3) == 2000


def test_lomas_no_self_excuse():
    res = lomas(counterparty_default_occurred=[0] * 4, counterparty_default_continuing=[0] * 4,
                payer_default_occurred=[1] * 4, payer_default_continuing=[1] * 4)
    assert statuses(res) == ["operative"] * 4
    assert all(res.records("payment_duty")[t]["satisfied"] for t in range(4))
    assert [flow(res, "payee_cash", t) for t in range(4)] == [1000] * 4


def test_lomas_termination_extinguishes_for_good():
    res = lomas(agreement_terminated=[0, 0, 1, 0])
    assert statuses(res) == ["suspended", "suspended", "extinguished", "extinguished"]
    assert res.group.books["deal"].postings == ()


def test_lomas_both_parties_defaulted_still_suspended():
    assert statuses(lomas(payer_default_continuing=[1] * 4)) == ["suspended"] * 4


def test_lomas_the_demand_is_inert():
    base, swung = lomas(), lomas(payment_demanded=[0] * 4)
    assert [dict(r) for r in swung.records("payment_duty").values()] == \
        [dict(r) for r in base.records("payment_duty").values()]
