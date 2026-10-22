"""M3 — the determination layer: OP5/OP6 functional; three oracle
reproductions through the engine as pure configs.

The expected values below are PUBLISHED rulings from the esco-moneyflow
oracle campaign (blind_briefs/orc-{or18,hel12,lomas}.md, each independently
reproduced blind by a prior engineer — blind-build-{or18,hel12,lomas}/):

  ORNL 2018 (orc-or18.md): 167 guaranteed-savings projects, honest annual
  M&V verification gating settlement, conservatively buffered guarantees
  (buffer beta, so the guarantee sits beta below expected performance).
  EXPECT: portfolio realization R = sum(verified)/sum(guaranteed) ~= 1/(1-beta);
  share of projects at/above guarantee ~= Phi(beta/sigma). With the recorded
  parameterization (beta=10%, verification noise 5%, performance dispersion
  10%, expected savings 44% of baseline, lognormal baselines median 100k,
  seed 2018): 111.1% and 81.3% (MC means; published DOE figures 108.0% and
  90.4% — the direction, not the parameter magnitude, is the mechanism).

  ISDA Hellenic 2012 (orc-hel12.md): the Restructuring Credit Event
  definition as a constitutive norm set + the Greek facts as state config.
  EXPECT: determination YES, limbs (a)(b)(c) all TRUE; leave-one-out flips
  on the principal-reduction, approval and binding facts (FB/FC/FD), not on
  the background facts (FA/FE).

  Lomas (orc-lomas.md): the counterparty-default condition precedent as an
  obligation norm + the facts. EXPECT: SUSPENDED every period; no self-excuse
  (the payer's own default is invisible to its duty); cure revives; only
  termination extinguishes.

If an expected value fails, the ENGINE (or a fixture config) is wrong —
never the expected value. The ensemble under ORNL is generated test-side
from a seeded generator (data-driven: 167 deal configs, no per-deal engine
code); the two determination fixtures are data files under tests/fixtures/.
"""
import math
import random
from pathlib import Path

import pytest
import yaml

from oe.engine.deal import Deal, DealConfigError, Instrument, Party, load_deal
from oe.engine.operators import Session
from oe.engine.spec import load_spec

KIT = Path(__file__).resolve().parent.parent
FIXTURES = KIT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def specs():
    return load_spec(KIT / "operators.yaml")


def _run(name, specs):
    session = Session(load_deal(FIXTURES / f"{name}.yaml", specs), specs)
    return session, session.run()


def _tmp(tmp_path, doc, name="deal.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(yaml.safe_load(doc) if isinstance(doc, str) else doc))
    return p


def _swing(tmp_path, base_name, mutate, specs, name="swung.yaml"):
    """Re-run a fixture with one config mutation (facts swung / popped) —
    the deterministic sensitivity device."""
    doc = yaml.safe_load((FIXTURES / f"{base_name}.yaml").read_text())
    mutate(doc)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(doc))
    session = Session(load_deal(p, specs), specs)
    return session, session.run()


# =============================================================================
# ORNL 2018 — realization under verification-gated settlement (the ensemble)
# =============================================================================

N_PROJECTS = 167
SEED = 2018
EXPECTED_SAVINGS = 0.44          # a, of baseline B0 (the guarantee lands at the
GUARANTEE_BUFFER = 0.10          # beta: G = a(1-beta)B0 ~ the 40% worked case
SIGMA_PERFORMANCE = 0.10         # cross-project dispersion of actual vs expected
SIGMA_VERIFICATION = 0.05        # honest zero-mean M&V noise
CYCLES = 48                      # annual verification snapshots (seeded)
CYCLES_DECOMP = 16

_ENSEMBLE_CLOCK = {"period_granularity": "year",
                   "phases": ["open", "accrue", "settle", "close"]}
_ENSEMBLE_ACCOUNTS = ["measured_world", "verified_savings", "provider_cash"]
_ENSEMBLE_PARTIES = (Party("provider", "provider"), Party("client", "client"))


def _project(b0, performance, verification):
    """One portfolio project as a deal config: the measurement gate verifies
    (accrue), the settlement pays the verified savings to the provider
    (settle) — payment follows VERIFIED savings."""
    return Deal(
        name="portfolio-project", parties=_ENSEMBLE_PARTIES,
        settings={"op7_clock": dict(_ENSEMBLE_CLOCK),
                  "op8_ledger": {"accounts": list(_ENSEMBLE_ACCOUNTS)}},
        instruments=(
            Instrument("op5_measurement_gate", {
                "mnv_option": "annual-verification",
                "verifier_gate": "third_party_confirm",
                "baseline_ref": round(b0, 6),
                "performance": round(performance, 6),
                "verification": round(verification, 6),
                "source": "measured_world",
                "verified_to": "verified_savings",
                "schedule_ref": {"start": 0, "terms": 1, "phase": "accrue"}}),
            Instrument("op2_sharing_rules", {
                "measured_from": "verified_savings",
                "split_fraction": 1.0,
                "payee": "provider_cash",
                "schedule_ref": {"start": 0, "terms": 1, "phase": "settle"}}),
        ), horizon=1)


def _settled_savings(b0, performance, verification, specs):
    """Run one project through the engine; return the SETTLED (posted)
    verified savings. Identities must be green wherever postings occur."""
    session = Session(_project(b0, performance, verification), specs)
    trace = session.run()
    for label, ids in trace:
        assert ids["duality"] and ids["sum_zero"] and ids["assertions"], (label, ids)
    # the gate stocked the resource; the settlement drained it back to zero
    assert session.ledger.closing_balance("verified_savings", 0) == 0
    return float(session.ledger.flow("provider_cash", 0))


def _ensemble(specs, cycles, buffer, seed=SEED):
    """The portfolio statistic over `cycles` annual verification snapshots of
    ONE seeded fixed portfolio: (realization R, share at/above guarantee)."""
    rng = random.Random(seed)
    b0 = [rng.lognormvariate(math.log(100000.0), 0.5) for _ in range(N_PROJECTS)]
    guarantee = [EXPECTED_SAVINGS * (1 - buffer) * b for b in b0]
    g_sum = sum(guarantee)
    realization_sum, at_or_above, n = 0.0, 0, 0
    for _ in range(cycles):
        v_sum = 0.0
        for i in range(N_PROJECTS):
            performance = EXPECTED_SAVINGS * (1 + rng.gauss(0.0, SIGMA_PERFORMANCE))
            verification = 1 + rng.gauss(0.0, SIGMA_VERIFICATION)
            v = _settled_savings(b0[i], performance, verification, specs)
            v_sum += v
            at_or_above += v >= guarantee[i]
            n += 1
        realization_sum += v_sum / g_sum
    return realization_sum / cycles, at_or_above / n


@pytest.fixture(scope="module")
def or18(specs, tmp_path_factory):
    # the generated configs are loader-valid (one full round trip; the
    # ensemble then drives the same shape as pure data)
    rng = random.Random(SEED)
    b0 = rng.lognormvariate(math.log(100000.0), 0.5)
    template = {
        "deal": "portfolio-project", "horizon": 1,
        "settings": {"op7_clock": dict(_ENSEMBLE_CLOCK),
                     "op8_ledger": {"accounts": list(_ENSEMBLE_ACCOUNTS)}},
        "parties": [{"name": "provider", "role": "provider"},
                    {"name": "client", "role": "client"}],
        "instruments": [
            {"operator": "op5_measurement_gate",
             "mnv_option": "annual-verification",
             "verifier_gate": "third_party_confirm",
             "baseline_ref": round(b0, 6), "performance": 0.44,
             "verification": 1.0, "source": "measured_world",
             "verified_to": "verified_savings",
             "schedule_ref": {"start": 0, "terms": 1, "phase": "accrue"}},
            {"operator": "op2_sharing_rules", "measured_from": "verified_savings",
             "split_fraction": 1.0, "payee": "provider_cash",
             "schedule_ref": {"start": 0, "terms": 1, "phase": "settle"}}]}
    p = tmp_path_factory.mktemp("or18") / "template.yaml"
    p.write_text(yaml.safe_dump(template))
    Session(load_deal(p, specs), specs).run()
    return _ensemble(specs, CYCLES, GUARANTEE_BUFFER)


def test_or18_realization_rate_oracle(or18):
    """Computed vs published: R ~ 1/(1-beta) = 111.1% (MC reproduction;
    published DOE figure 108.0% — same direction, the buffer is the cause)."""
    realization, _ = or18
    assert realization * 100 == pytest.approx(111.1, abs=0.8)
    assert realization == pytest.approx(1 / (1 - GUARANTEE_BUFFER), abs=0.008)


def test_or18_share_at_or_above_guarantee_oracle(or18):
    """Computed vs published: share ~ Phi(beta/sigma) = 81.3% (MC
    reproduction; published DOE figure 90.4%)."""
    _, share = or18
    assert share * 100 == pytest.approx(81.3, abs=1.5)
    sigma = math.sqrt(SIGMA_PERFORMANCE ** 2 + SIGMA_VERIFICATION ** 2)
    phi = 0.5 * (1 + math.erf((GUARANTEE_BUFFER / sigma) / math.sqrt(2)))
    assert share == pytest.approx(phi, abs=0.025)


def test_or18_zero_buffer_decomposition(specs):
    """The mechanism, not the parameter: with no buffer (beta = 0) the same
    engine returns exactly 100% and a coin-flip 50% at/above share — the
    elevation is entirely the conservative guarantee buffer."""
    realization, share = _ensemble(specs, CYCLES_DECOMP, 0.0)
    assert realization * 100 == pytest.approx(100.0, abs=0.6)
    assert share * 100 == pytest.approx(50.0, abs=2.5)


def test_or18_the_gate_shuts_off_unverified_savings(tmp_path, specs):
    """The settlement gate: when the verifier confirms nothing (verification
    factor 0), no savings exist as a matter of record — nothing posts, the
    settlement flows nothing."""
    gated = {
        "deal": "gated-project", "horizon": 1,
        "settings": {"op7_clock": dict(_ENSEMBLE_CLOCK),
                     "op8_ledger": {"accounts": list(_ENSEMBLE_ACCOUNTS)}},
        "parties": [{"name": "provider", "role": "provider"}],
        "instruments": [
            {"operator": "op5_measurement_gate",
             "mnv_option": "annual-verification",
             "verifier_gate": "third_party_confirm",
             "baseline_ref": 100000.0, "performance": 0.44,
             "verification": 0.0, "source": "measured_world",
             "verified_to": "verified_savings",
             "schedule_ref": {"start": 0, "terms": 1, "phase": "accrue"}},
            {"operator": "op2_sharing_rules", "measured_from": "verified_savings",
             "split_fraction": 1.0, "payee": "provider_cash",
             "schedule_ref": {"start": 0, "terms": 1, "phase": "settle"}}]}
    session = Session(load_deal(_tmp(tmp_path, gated, "gated.yaml"), specs), specs)
    trace = session.run()
    assert all(ids["duality"] and ids["sum_zero"] for _, ids in trace)
    assert len(session.ledger.postings()) == 0          # nothing verified: nothing books
    assert session.ledger.flow("provider_cash", 0) == 0  # unverified savings don't flow


# =============================================================================
# ISDA Hellenic 2012 — the deontic event determination
# =============================================================================

@pytest.fixture(scope="module")
def hel12(specs):
    return _run("hel12", specs)


def test_hel12_determination_yes_with_three_condition_breakdown(hel12):
    session, trace = hel12
    assert all(ids["duality"] and ids["sum_zero"] and ids["assertions"] for _, ids in trace)
    assert len(session.ledger.postings()) == 0           # a pure determination posts nothing
    rec = session.commitments["named-event"][0]
    assert rec["occurs"] is True                          # the determination: YES
    assert rec["limbs"] == {"reduction_or_deferral": True,      # (a) 53.5% principal cut
                            "direction_or_consent": True,       # (b) government directed/consented
                            "binds_holders": True}              # (c) supermajority binds all


# the published leave-one-out table: the fact sentence -> limb breakdown flips
LOO_TABLE = {
    "government_enacted_cac_after_issuance": True,   # background — still YES
    "principal_reduction_fraction": False,           # FB — limb (a) loses its only support: NO
    "action_launched_by_government": True,           # (b) keeps direction+consent: YES
    "supermajority_approved": False,                 # FC — limb (c) exercise gone: NO
    "supermajority_acceptance_binds_all": False,     # FD — limb (c) force gone: NO
    "government_directed_action": True,              # (b) keeps consent+launch: YES
    "government_consented_to_action": True,          # (b) keeps direction+launch: YES
}


@pytest.mark.parametrize("fact", LOO_TABLE)
def test_hel12_leave_one_out_flip_behavior(tmp_path, specs, fact):
    """Remove one fact at a time (closed world: absent facts are false); the
    determination flips exactly on the principal-reduction, approval and
    binding facts — the published flipping-fact analysis."""
    def drop(doc):
        del doc["instruments"][0]["conditions"][fact]
    session, _ = _swing(tmp_path, "hel12", drop, specs,
                        name=f"loo-{fact}.yaml")
    rec = session.commitments["named-event"][0]
    assert rec["occurs"] is LOO_TABLE[fact], fact
    if fact == "principal_reduction_fraction":
        assert rec["limbs"]["reduction_or_deferral"] is False   # the named flip: (a) dies


def test_hel12_government_pair_removal_still_yes(tmp_path, specs):
    """Removing BOTH government attribution facts (the FE sentence) leaves
    limb (b) standing a fortiori — the government itself launched the action."""
    def drop(doc):
        conditions = doc["instruments"][0]["conditions"]
        del conditions["government_directed_action"]
        del conditions["government_consented_to_action"]
    session, _ = _swing(tmp_path, "hel12", drop, specs, name="loo-fe-pair.yaml")
    rec = session.commitments["named-event"][0]
    assert rec["limbs"]["direction_or_consent"] is True
    assert rec["occurs"] is True


# =============================================================================
# Lomas — the condition-precedent suspension
# =============================================================================

@pytest.fixture(scope="module")
def lomas(specs):
    return _run("lomas", specs)


def test_lomas_classification_suspended(lomas):
    """The published ruling: the payment obligation is SUSPENDED — the payer
    does not have to pay now; nothing posts; the agreement stays alive."""
    session, trace = lomas
    assert all(ids["duality"] and ids["sum_zero"] and ids["assertions"] for _, ids in trace)
    duty = session.commitments["payment-duty"]
    assert len(duty) == 4                                    # every period classified
    for t, rec in duty.items():
        assert rec == {"status": "suspended"}, (t, rec)
    assert len(session.ledger.postings()) == 0               # the state gates posting
    assert session.ledger.flow("payee_cash", 3) == 0


def test_lomas_cure_revives_the_duty(tmp_path, specs):
    """The payee's Event of Default stops continuing -> the precedent holds
    again -> the duty revives automatically (no new agreement) and payments
    resume exactly from that period."""
    def cure(doc):
        doc["instruments"][0]["conditions"]["counterparty_default_continuing"] = \
            [True, True, False, False]
    session, trace = _swing(tmp_path, "lomas", cure, specs, name="lomas-cure.yaml")
    assert all(ids["duality"] and ids["sum_zero"] for _, ids in trace)
    duty = session.commitments["payment-duty"]
    assert [duty[t]["status"] for t in range(4)] == \
        ["suspended", "suspended", "operative", "operative"]
    assert session.ledger.flow("payer_cash", 1) == 0         # suspended: no payment
    for t in (2, 3):
        assert float(session.ledger.flow("payee_cash", t)) == 1000.0   # revived: pays
    assert float(session.ledger.closing_balance("payee_cash", 3)) == 2000.0


def test_lomas_no_self_excuse(tmp_path, specs):
    """A party's own default does NOT excuse it: the payer clean of any
    counterparty default stays operative and pays every period even while
    ITSELF in a continuing Event of Default (the precedent never consults
    the bearer's own affairs)."""
    def self_default(doc):
        conditions = doc["instruments"][0]["conditions"]
        conditions["counterparty_default_occurred"] = False
        conditions["counterparty_default_continuing"] = False
        conditions["payer_default_occurred"] = True      # read by no expression
        conditions["payer_default_continuing"] = True
    session, _ = _swing(tmp_path, "lomas", self_default, specs, name="lomas-self.yaml")
    duty = session.commitments["payment-duty"]
    for t in range(4):
        assert duty[t]["status"] == "operative", t
        assert duty[t].get("satisfied") is True, t
        assert float(session.ledger.flow("payee_cash", t)) == 1000.0, t


def test_lomas_termination_extinguishes_terminally(tmp_path, specs):
    """Termination is the extinction route — and it is TERMINAL: the duty
    stays extinguished even if the terminated fact later reads false again."""
    def terminate(doc):
        doc["instruments"][0]["conditions"]["agreement_terminated"] = \
            [False, False, True, False]
    session, _ = _swing(tmp_path, "lomas", terminate, specs, name="lomas-term.yaml")
    duty = session.commitments["payment-duty"]
    assert [duty[t]["status"] for t in range(4)] == \
        ["suspended", "suspended", "extinguished", "extinguished"]
    assert len(session.ledger.postings()) == 0


def test_lomas_both_parties_defaulted_still_suspended(tmp_path, specs):
    """Only the counterparty's default matters: with BOTH in continuing
    default, the payer's duty is suspended exactly as in the base case."""
    def both(doc):
        doc["instruments"][0]["conditions"]["payer_default_continuing"] = True
    session, _ = _swing(tmp_path, "lomas", both, specs, name="lomas-both.yaml")
    duty = session.commitments["payment-duty"]
    for t in range(4):
        assert duty[t]["status"] == "suspended", t


def test_lomas_demand_is_inert_and_rerun_is_deterministic(lomas, specs, tmp_path):
    """The payee's demand alters nothing (no expression reads it), and the
    same config re-evaluates identically (determinism)."""
    session, _ = lomas
    def drop_demand(doc):
        del doc["instruments"][0]["conditions"]["payment_demanded"]
    swung, _ = _swing(tmp_path, "lomas", drop_demand, specs, name="lomas-nodemand.yaml")
    assert swung.commitments == session.commitments
    again = Session(load_deal(FIXTURES / "lomas.yaml", specs), specs)
    again.run()
    assert again.commitments == session.commitments
    assert [(p.label, p.legs) for p in again.ledger.postings()] == \
        [(p.label, p.legs) for p in session.ledger.postings()]


# =============================================================================
# unit level: the generic determination dials the fixtures do not exercise
# =============================================================================

GATE_DEAL = """
deal: unit-gate
horizon: 3
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [measured_world, verified_savings, client_cash, claim_client,
                          collections_pool, provider_cash]}
parties:
  - {name: client, role: client}
  - {name: provider, role: provider}
instruments:
  - operator: op5_measurement_gate
    mnv_option: retrofit-isolation
    verifier_gate: third_party_confirm
    baseline_ref: {baseline_bill: 1000.0, savings_fraction: 0.5}
    adjustment_variables: {degree_days: [1.0, 1.1, 0.9]}
    performance: [1.0, 0.8, 1.2]
    verification: [1.0, 0.5, 0.0]
    source: measured_world
    verified_to: verified_savings
    schedule_ref: {start: 0, terms: 3, phase: accrue}
  - operator: op2_sharing_rules
    measured_from: verified_savings
    split_fraction: 1.0
    payee: claim_client
    schedule_ref: {start: 0, terms: 3, phase: settle}
  - operator: op3_ranked_distribution
    level_order: [{claim: claim_client, payee: provider_cash, kind: variable}]
    collect_from: client_cash
    available_from: collections_pool
    schedule_ref: {start: 0, terms: 3, phase: settle}
"""


def test_op5_adjustment_paths_and_the_full_gate_chain(tmp_path, specs):
    """The verified quantity is baseline x adjustments x performance x the
    verification factor, per period, exact; and the gate carries through the
    whole chain — a claim stocked by a measured_from split accrues ONLY what
    was verified, so in the unverified period NOTHING flows: no accrual, no
    collection, no distribution. (Composition note: the waterfall's mirror
    refunds the resource leg that funded the accrual — the M2 discharge
    semantics — so the resource account registers the gross verified
    quantity; per-party cash is exact either way.)"""
    session = Session(load_deal(_tmp(tmp_path, GATE_DEAL), specs), specs)
    trace = session.run()
    assert all(ids["duality"] and ids["sum_zero"] and ids["assertions"] for _, ids in trace)
    ledger = session.ledger
    # period 0: 1000 x 0.5 x 1 x 1.0 = 500 verified, accrued as claim, distributed
    assert float(ledger.flow("provider_cash", 0)) == 500.0
    # period 1: 1000 x 0.5 x 1.1 x 0.8 x 0.5 = 220
    assert float(ledger.flow("provider_cash", 1)) == 220.0
    # period 2: verification 0 — nothing exists as a matter of record: the
    # claim never accrues, the waterfall distributes nothing, no cash moves
    assert float(ledger.flow("provider_cash", 2)) == 0.0
    assert ledger.flow("claim_client", 2) == 0
    assert ledger.flow("client_cash", 2) == 0
    for t in range(3):
        assert ledger.closing_balance("claim_client", t) == 0   # settles back each period
        assert ledger.closing_balance("collections_pool", t) == 0


VIOLATION_DEAL = """
deal: unit-violation
horizon: 4
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [payer_cash, payee_cash]}
parties:
  - {name: payer-party, role: payer}
  - {name: payee-party, role: payee}
instruments:
  - operator: op6_deontic_evaluation
    norm_set:
      - id: duty
        kind: obligation
        bearer: payer-party
        counterparty: payee-party
        performance: {payer: payer_cash, payee: payee_cash, amount: 100.0,
                      performed: {fact: bearer_paid}}
    conditions:
      bearer_paid: [true, false, false, true]
      breach_cured: [false, false, true, false]
    remedies:
      duty: {payer: payer_cash, payee: payee_cash, amount: 40.0}
    cure_rules:
      duty: {fact: breach_cured}
    schedule_ref: {start: 0, terms: 4, phase: settle}
"""


def test_op6_violation_reparation_cure_machinery(tmp_path, specs):
    """The violation -> reparation -> cure transitions: a due-and-unperformed
    duty is violated (nothing posts), the reparation posts ONCE per episode,
    and the cure expression ends the episode — performance then resumes."""
    session = Session(load_deal(_tmp(tmp_path, VIOLATION_DEAL), specs), specs)
    trace = session.run()
    assert all(ids["duality"] and ids["sum_zero"] and ids["assertions"] for _, ids in trace)
    duty = session.commitments["duty"]
    assert duty[0] == {"status": "operative", "satisfied": True}
    assert duty[1] == {"status": "operative", "violated": True,
                       "remedy_due": True, "remedy_paid": True}
    assert duty[2] == {"status": "operative", "violated": True, "cured": True}
    assert duty[3] == {"status": "operative", "satisfied": True}
    # the posting gate: payment when performed, the reparation when violated
    flows = [float(session.ledger.flow("payee_cash", t)) for t in range(4)]
    assert flows == [100.0, 40.0, 0.0, 100.0]


PROHIBITION_DEAL = """
deal: unit-prohibition
horizon: 4
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [operator_cash, pool_cash]}
parties:
  - {name: operator-party, role: servicer}
instruments:
  - operator: op6_deontic_evaluation
    norm_set:
      - id: no-diversion
        kind: prohibition
        bearer: operator-party
        forbidden: {fact: collections_diverted}
    conditions:
      collections_diverted: [false, true, true, false]
      diversion_undone: [false, false, true, false]
    remedies:
      no-diversion: {payer: operator_cash, payee: pool_cash, amount: 25.0}
    cure_rules:
      no-diversion: {fact: diversion_undone}
    schedule_ref: {start: 0, terms: 4, phase: settle}
"""


def test_op6_prohibition_violation_and_cure(tmp_path, specs):
    session = Session(load_deal(_tmp(tmp_path, PROHIBITION_DEAL), specs), specs)
    trace = session.run()
    assert all(ids["duality"] and ids["sum_zero"] for _, ids in trace)
    norm = session.commitments["no-diversion"]
    assert norm[0] == {"status": "operative"}
    assert norm[1] == {"status": "operative", "violated": True,
                       "remedy_due": True, "remedy_paid": True}
    assert norm[2] == {"status": "operative", "violated": True, "cured": True}
    assert norm[3] == {"status": "operative"}
    assert float(session.ledger.flow("pool_cash", 1)) == 25.0   # the reparation


def test_op5_self_reported_refuses_a_verification_factor(tmp_path, specs):
    doc = yaml.safe_load(GATE_DEAL)
    doc["instruments"][0]["verifier_gate"] = "self_reported"
    with pytest.raises(DealConfigError, match="self_reported"):
        Session(load_deal(_tmp(tmp_path, yaml.safe_dump(doc)), specs), specs)


def test_op6_refuses_unknown_expression_nodes(tmp_path, specs):
    doc = yaml.safe_load(VIOLATION_DEAL)
    doc["instruments"][0]["norm_set"][0]["condition_precedent"] = {"maybe": {"fact": "x"}}
    with pytest.raises(DealConfigError, match="expression"):
        Session(load_deal(_tmp(tmp_path, yaml.safe_dump(doc)), specs), specs)
    doc = yaml.safe_load(VIOLATION_DEAL)
    doc["instruments"][0]["remedies"] = {"unknown-norm": {"payer": "payer_cash",
                                                          "payee": "payee_cash",
                                                          "amount": 1.0}}
    with pytest.raises(DealConfigError, match="names no norm"):
        Session(load_deal(_tmp(tmp_path, yaml.safe_dump(doc)), specs), specs)


def test_op2_refuses_declaring_both_quantity_sources(tmp_path, specs):
    doc = yaml.safe_load(GATE_DEAL)
    doc["instruments"][1]["measured_input"] = 500.0
    with pytest.raises(DealConfigError, match="not both"):
        Session(load_deal(_tmp(tmp_path, yaml.safe_dump(doc)), specs), specs)


# =============================================================================
# the birth-certificate statement, extended to M3
# =============================================================================

def test_the_engine_names_no_case():
    """Config-only reproduction, M3 evidence: the engine package and the
    operator spec contain no case name, party name or ruling vocabulary of
    the three determination oracles (as words) — the fixtures are YAML data
    files, the ensemble is a seeded test-side generator, and no engine
    behavior is keyed to a case."""
    import re

    def words(text, *needles):
        return [n for n in needles if re.search(rf"\b{re.escape(n)}\b", text)]

    engine = "\n".join(p.read_text() for p in (KIT / "oe" / "engine").glob("*.py")).lower()
    assert not words(engine, "ornl", "or18", "hellenic", "isda", "lomas",
                     "supermajority", "haircut", "restructuring")
    spec_text = (KIT / "operators.yaml").read_text().lower()
    assert not words(spec_text, "ornl", "or18", "hellenic", "isda", "lomas",
                     "supermajority", "haircut", "restructuring")
