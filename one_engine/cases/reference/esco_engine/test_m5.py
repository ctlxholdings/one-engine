"""M5 — the growth layer: OP10 platform aggregation functional; the pooled
guaranteed-savings structure reproduced at Berlin scale through the engine
as one pure config.

THE BERLIN FIXTURE (case-register C23 + the campaign's step-2 structural
beds): the Berlin Energy Saving Partnership — 1,400+ public buildings deep-
retrofitted under pooled guaranteed-savings lots, running since 1996. This
is a STRUCTURE + SCALE reproduction (the bed's recorded acceptance level —
no closed-form inputs are published): a seeded test-side generator produces
~1,400 building sub-deal configs (baseline consumption drawn lognormal,
savings fraction ~25.5% average cut, per-lot cycle lengths), pools them in
lots through OP10 (one platform pool; lots are the origination schedule's
grouping — buildings enter with their lot, one lot rolls over mid-run),
accrues the platform's services fee from the AGGREGATED pool flow (OP4),
ranks it over the aggregated availability behind a pool reserve (OP3), and
reduces pool-level metrics (OP9).

  PUBLISHED (the oracles — if one fails, the ENGINE or a fixture config is
  wrong, never the number):
    ~EUR 12.5m guaranteed savings per cycle   (annual verification cycle)
    ~25.5% average energy cut
    EUR 2.7-2.8m/yr net to the public budget
    1,400+ buildings, pooled in lots, running since 1996

  DERIVED (asserted for CONSISTENCY, never claimed as published): the
  implied annual management/energy-services fee = savings - net ~
  EUR 9.7-9.8m/yr. The published PAIR implies it; the fee itself was not
  published. The fixture configures the client's net share from the
  published pair (2.75/12.5 = 22%) and the fee claim as the residual 78%.

  CALIBRATION (test-side data, recorded): baseline bills lognormal median
  EUR 35,000, sigma 0.45 (stock total ~EUR 49m ~= 12.5m / 0.255); savings
  fractions Normal(25.5%, 2%) clipped to [10%, 45%]; the stock then SCALED
  by a single factor (0.889274 at seed 1996) so the aggregate guaranteed
  savings land at EUR 12.5m/cycle. 1,400 buildings shuffled into 46 lots
  (20 x 31 + 26 x 30); lots enter staggered over 5 periods (10/10/10/10/6
  lots); lot 00 (the pilot) runs an 8-year cycle and ROLLS OVER mid-run
  into a second cycle. Seed 1996 (the year the partnership started).

Semantic references for OP10's corpus math (consulted, not reproduced):
the register's pool template F6 (validation_targets.calibration_cases.pool_template, formerly policies.entries.pool_economics; static pool, approximation) and
the securitization policy F5 (tranche distributions after reserve top-ups)
— the ACTUS securitization anchor covers pool composition only, and OP10
stays a composition over the core (tranching/waterfall is OP3's).
"""
import math
import random
import re
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from oe.engine.deal import DealConfigError, load_deal
from oe.engine.ledger import LedgerError
from oe.engine.operators import Session
from oe.engine.spec import load_spec

KIT = Path(__file__).resolve().parent.parent
FIXTURES = KIT / "tests" / "fixtures"


@pytest.fixture(scope="module")
def specs():
    return load_spec(KIT / "operators.yaml")


def _green(trace):
    for label, ids in trace:
        assert ids["duality"] and ids["sum_zero"] and ids["assertions"], (label, ids)


# =============================================================================
# THE BERLIN FIXTURE — the seeded generator (test-side data, no engine code)
# =============================================================================

SEED = 1996                       # the year the partnership started
N_BUILDINGS = 1400                # published: 1,400+ buildings
N_LOTS = 46                       # 20 x 31 + 26 x 30 buildings per lot
BILL_MEDIAN, BILL_SIGMA = 35000.0, 0.45
CUT_MEAN, CUT_SD = 0.255, 0.02    # published: ~25.5% average energy cut
SAVINGS_TARGET = 12_500_000.0     # published: ~EUR 12.5m guaranteed savings/cycle
NET_LOW, NET_HIGH = 2_700_000.0, 2_800_000.0   # published: EUR 2.7-2.8m/yr net
NET_ANNUAL = 2_750_000.0          # the pair's midpoint (the config key)
FEE_ANNUAL = SAVINGS_TARGET - NET_ANNUAL       # 9.75m — the IMPLIED fee (derived)
CLIENT_SHARE = Fraction(str(NET_ANNUAL / SAVINGS_TARGET))    # 11/50, from the pair
FEE_SHARE = 1 - CLIENT_SHARE                                  # 39/50, the residual
HORIZON = 15
FIRST_TERMS, ROLLOVER_AT = 8, 8   # lot 00's pilot cycle, then the rollover
RESERVE_TARGET = 500_000.0        # a pool working-capital reserve (modeling
                                  # convention: the public budget never funds
                                  # it — see the year-0 assertion)

POOL = "the-platform"


def _berlin_config():
    """The seeded generator -> (deal doc as pure data, manifest for tests)."""
    rng = random.Random(SEED)
    bills = [rng.lognormvariate(math.log(BILL_MEDIAN), BILL_SIGMA)
             for _ in range(N_BUILDINGS)]
    cuts = [min(max(rng.gauss(CUT_MEAN, CUT_SD), 0.10), 0.45)
            for _ in range(N_BUILDINGS)]
    # the calibration: scale the stock so the BOOKED quantities (each factor
    # lands 6dp through the loader) sum to the published per-cycle level —
    # the rounding residual is absorbed by one scale refinement
    scale = SAVINGS_TARGET / sum(b * c for b, c in zip(bills, cuts))
    for _ in range(8):
        booked_total = sum(Fraction(str(round(b * scale, 6)))
                           * Fraction(str(round(c, 6)))
                           for b, c in zip(bills, cuts))
        if abs(float(booked_total) - SAVINGS_TARGET) < 0.005:
            break
        scale *= SAVINGS_TARGET / float(booked_total)
    order = list(range(N_BUILDINGS))
    rng.shuffle(order)
    sizes = [N_BUILDINGS // N_LOTS + (1 if j < N_BUILDINGS % N_LOTS else 0)
             for j in range(N_LOTS)]
    lots, at = [], 0
    for size in sizes:
        lots.append(order[at:at + size])
        at += size

    members, entries, booked = [], [], {}
    for j, lot in enumerate(lots):
        entry = j // 10                                   # staggered lot origination
        terms = HORIZON - entry
        stints = [(0, FIRST_TERMS), (ROLLOVER_AT, HORIZON - ROLLOVER_AT)] if j == 0 \
            else [(entry, terms)]
        for i in lot:
            mid = f"bldg-{i:04d}"
            bill = round(bills[i] * scale, 6)
            cut = round(cuts[i], 6)
            members.append({"id": mid, "quantity": {"baseline_bill": bill,
                                                    "savings_fraction": cut},
                            "source": "building_stock_bills",
                            "member_to": f"{mid}_savings"})
            booked[mid] = Fraction(str(bill)) * Fraction(str(cut))
            for e, n in stints:
                entries.append({"member": mid, "entry": e, "terms": n})

    accounts = ["building_stock_bills", "platform_pool", "platform_consolidation",
                "platform_operations", "services_fee_claim", "client_net_claim",
                "services_cash", "client_budget", "pool_reserve",
                "waterfall_availability"] + [f"bldg-{i:04d}_savings"
                                             for i in range(N_BUILDINGS)]
    doc = {
        "deal": "pooled-guaranteed-savings-platform",
        "horizon": HORIZON,
        "settings": {
            "op7_clock": {"period_granularity": "year",
                          "phases": ["open", "accrue", "settle", "close"]},
            "op8_ledger": {"accounts": accounts},
        },
        "parties": [
            {"name": "platform-operator", "role": "servicer"},
            {"name": "public-client", "role": "client"},
            {"name": "building-stock", "role": "world"},
        ],
        "instruments": [
            {"operator": "op10_platform_aggregation",
             "pool_composition": {"pools": [
                 {"id": POOL, "pool_to": "platform_pool",
                  "aggregate_from": "platform_consolidation",
                  "members": members}]},
             "origination_flow": {"entries": entries},
             "terminal_value_rule": {"kind": "distribute_residual",
                                     "accounts": ["pool_reserve"],
                                     "to": "client_budget", "at": HORIZON - 1}},
            {"operator": "op4_transform",
             "index_ref": float(FEE_SHARE),          # 0.78 — the implied fee share
             "apply_to": {"account": "platform_pool", "read": "flow"},
             "payer": "platform_operations", "payee": "services_fee_claim",
             "schedule_ref": {"start": 0, "terms": HORIZON, "phase": "settle"}},
            {"operator": "op4_transform",
             "index_ref": float(CLIENT_SHARE),       # 0.22 — from the published pair
             "apply_to": {"account": "platform_pool", "read": "flow"},
             "payer": "platform_operations", "payee": "client_net_claim",
             "schedule_ref": {"start": 0, "terms": HORIZON, "phase": "settle"}},
            {"operator": "op3_ranked_distribution",
             "level_order": [
                 {"claim": "services_fee_claim", "payee": "services_cash",
                  "kind": "fixed"},
                 {"claim": "client_net_claim", "payee": "client_budget",
                  "kind": "variable"}],
             "collect_from": "platform_pool",
             "available_from": "waterfall_availability",
             "reserve_targets": {"pool_reserve": RESERVE_TARGET},
             "schedule_ref": {"start": 0, "terms": HORIZON, "phase": "settle"}},
            {"operator": "op9_metric_reduction",
             "metrics": ["position", "horizon"], "horizon": HORIZON,
             "position_scope": ["platform_pool", "platform_consolidation",
                                "services_cash", "client_budget", "pool_reserve"]},
        ],
    }
    manifest = {"lots": lots, "booked": booked, "entries": entries,
                "scale": scale, "bills": bills, "cuts": cuts}
    return doc, manifest


@pytest.fixture(scope="module")
def berlin(specs, tmp_path_factory):
    doc, manifest = _berlin_config()
    path = tmp_path_factory.mktemp("berlin") / "berlin.yaml"
    path.write_text(yaml.safe_dump(doc))          # the config IS a YAML file
    session = Session(load_deal(path, specs), specs)
    trace = session.run()
    return session, trace, manifest


# =============================================================================
# the Berlin assertions (published pair + structural identities)
# =============================================================================

def _pool_additions(session, t):
    """The pool's gross receipts in period t (the corkscrew additions line)."""
    return session.ledger.corkscrew("platform_pool", t)[1]


def test_berlin_identities_green_every_period(berlin):
    session, trace, _ = berlin
    assert len(trace) == HORIZON
    _green(trace)                       # includes OP10's per-period composition
                                        # identity check (pool == sum of members)


def test_berlin_aggregate_guaranteed_savings_per_cycle(berlin):
    """The published ~EUR 12.5m guaranteed savings per (annual verification)
    cycle: once every lot has originated (t >= 4) the pool's gross receipts
    are EUR 12.5m every year — within the stated +-1% band, and (the
    calibration being exact) within a cent. The generated stock's average
    cut is 25.5%."""
    session, _, manifest = berlin
    full = [_pool_additions(session, t) for t in range(4, HORIZON)]
    assert all(a == full[0] for a in full)            # static stock: identical Fractions
    assert float(full[0]) == pytest.approx(12_500_000.0, abs=0.01)
    for a in full:                                    # the stated tolerance band
        assert abs(float(a) - 12_500_000.0) <= 0.01 * 12_500_000.0
    bills, cuts, scale = manifest["bills"], manifest["cuts"], manifest["scale"]
    assert sum(b * scale * c for b, c in zip(bills, cuts)) / (sum(bills) * scale) == \
        pytest.approx(0.255, abs=0.005)
    print(f"\naggregate guaranteed savings/cycle: EUR {float(full[0]):,.2f} "
          f"(published ~12.5m, band +-1%)")


def test_berlin_the_implied_fee_falls_out(berlin):
    """The implied fee: the published pair (12.5m savings, 2.7-2.8m net)
    implies an annual management/energy-services fee of ~9.7-9.8m — DERIVED,
    not published. The engine returns it as the platform's fee cash flow,
    and the conservation identity fee + net + dReserve == savings holds
    EXACTLY (Fractions) every period."""
    session, _, _ = berlin
    ledger = session.ledger
    for t in range(4, HORIZON - 1):                # steady full-pool years (the
        fee = ledger.flow("services_cash", t)      # final year carries the reserve's
        net = ledger.flow("client_budget", t)      # terminal release, asserted below)
        assert float(fee) == pytest.approx(9_750_000.0, abs=0.01)
        assert 9_700_000.0 <= float(fee) <= 9_800_000.0
        assert 2_700_000.0 <= float(net) <= 2_800_000.0
    for t in range(HORIZON):                      # conservation, exact, every period
        prev = ledger.closing_balance("pool_reserve", t - 1) if t else Fraction(0)
        d_reserve = ledger.closing_balance("pool_reserve", t) - prev
        assert ledger.flow("services_cash", t) + ledger.flow("client_budget", t) \
            + d_reserve == _pool_additions(session, t), t
    print(f"\nimplied fee: EUR {float(ledger.flow('services_cash', 5)):,.0f}/yr "
          f"(= 12.5m - 2.75m; DERIVED from the published pair, band 9.7-9.8m)")


def _member_balances(ledger, t):
    """One pass over the postings through t: every member sub-ledger's
    closing position (cheap at 1,400 accounts — no per-account re-scan)."""
    balances = {f"bldg-{i:04d}_savings": Fraction(0) for i in range(N_BUILDINGS)}
    for p in ledger.postings():
        if p.period > t:
            continue
        for leg in p.legs:
            if leg.account in balances:
                balances[leg.account] += leg.amount
    return balances


def test_berlin_pool_ledger_is_the_sum_of_the_sub_ledgers(berlin):
    """The enforced identity, read back test-side at scale: at sampled
    periods (ramp, full pool, either side of the rollover, final) the sum
    of the 1,400 per-building sub-ledger positions equals the pool's
    cumulative gross receipts and the elimination account's mirror; each
    building's own sub-ledger accumulates exactly its generated stream.
    (The engine itself enforces the identity at EVERY period close — the
    green trace above.)"""
    session, _, manifest = berlin
    ledger = session.ledger
    for t in (0, 4, 7, 8, HORIZON - 1):
        members_total = sum(_member_balances(ledger, t).values(), Fraction(0))
        received = sum((_pool_additions(session, s) for s in range(t + 1)), Fraction(0))
        assert members_total == received, t
        assert ledger.closing_balance("platform_consolidation", t) == -members_total, t
    # spot check: one pilot-lot building's sub-ledger across the rollover
    pilot_id = f"bldg-{manifest['lots'][0][0]:04d}"
    pilot, g = f"{pilot_id}_savings", manifest["booked"][pilot_id]
    assert ledger.closing_balance(pilot, 7) == 8 * g      # first cycle: 8 periods
    assert ledger.closing_balance(pilot, 8) == 9 * g      # rollover: no gap
    assert ledger.closing_balance(pilot, 14) == 15 * g
    assert ledger.flow(pilot, 7) == g and ledger.flow(pilot, 8) == g


def test_berlin_origination_flow_and_the_mid_run_rollover(berlin):
    """Origination as data: the lot entry schedule (10/10/10/10/6 lots over
    t=0..4) and lot 00's mid-run rollover (second stint entered at t=8) are
    recorded on session.pools; nothing is threshold-skipped; the platform's
    receipts ramp with the lots and hold at the full stock afterward."""
    session, _, manifest = berlin
    record = session.pools[POOL]
    expected = {0: 310, 1: 310, 2: 300, 3: 300, 4: 180, 8: 31}
    assert {t: len(rec["entered"]) for t, rec in record.items()
            if isinstance(t, int)} == expected
    assert all(not rec["skipped"] for rec in record.values()
               if isinstance(rec, dict) and "skipped" in rec)
    pilot_ids = {f"bldg-{i:04d}" for i in manifest["lots"][0]}
    assert pilot_ids <= set(record[0]["entered"])            # entered with lot 00
    assert pilot_ids <= set(record[8]["entered"])            # re-entered at rollover
    adds = [float(_pool_additions(session, t)) for t in range(HORIZON)]
    assert adds[0] < adds[4] and adds[3] < adds[4]           # the origination ramp
    assert adds[5] == adds[14]                               # then the full stock holds


def test_berlin_pool_reserve_waterfall_interaction(berlin):
    """The pool-level reserve/waterfall interaction: the reserve is topped to
    target from the AGGREGATED availability BEFORE the claims (the client's
    variable level absorbs the top-up in year 0), the public budget NEVER
    funds the pool (client flow >= 0 every period), and the terminal rule
    releases the reserve to the client at the final period."""
    session, _, _ = berlin
    ledger = session.ledger
    f0 = _pool_additions(session, 0)
    assert ledger.closing_balance("pool_reserve", 0) == Fraction(500000)
    for t in range(1, HORIZON - 1):
        assert ledger.closing_balance("pool_reserve", t) == Fraction(500000)
    assert ledger.flow("client_budget", 0) == CLIENT_SHARE * f0 - Fraction(500000)
    assert ledger.flow("client_budget", 0) > 0          # never funds the pool
    assert all(ledger.flow("client_budget", t) >= 0 for t in range(HORIZON))
    assert ledger.closing_balance("pool_reserve", HORIZON - 1) == 0   # terminal sweep
    assert ledger.flow("client_budget", HORIZON - 1) == \
        CLIENT_SHARE * _pool_additions(session, HORIZON - 1) + Fraction(500000)
    assert session.pools[POOL]["terminal"] == {
        "kind": "distribute_residual", "at": HORIZON - 1, "to": "client_budget",
        "swept": {"pool_reserve": Fraction(500000)}}


def test_berlin_positions_and_metrics_at_pool_level(berlin):
    """OP9 reductions over the pool level: the platform's accounts reduce to
    position series and horizon totals; lifetime conservation holds EXACTLY
    (fee total + net total == total guaranteed savings; the reserve round
    trips through the terminal sweep)."""
    session, _, _ = berlin
    ledger = session.ledger
    m = session.metrics
    assert set(m["position"]) == {"platform_pool", "platform_consolidation",
                                  "services_cash", "client_budget", "pool_reserve"}
    assert all(len(series) == HORIZON for series in m["position"].values())
    total_savings = sum((_pool_additions(session, t) for t in range(HORIZON)),
                        Fraction(0))
    fee_total = m["horizon"]["totals"]["services_cash"]
    net_total = m["horizon"]["totals"]["client_budget"]
    assert fee_total == FEE_SHARE * total_savings            # exact Fractions
    assert net_total == CLIENT_SHARE * total_savings
    assert fee_total + net_total == total_savings            # the implied-fee identity
    assert m["horizon"]["closing"]["pool_reserve"] == 0      # swept at terminal
    assert all(f == 0 for f in m["position"]["platform_pool"])  # fully collected


def test_berlin_deterministic(specs, tmp_path_factory, berlin):
    session, _, manifest = berlin
    doc, _ = _berlin_config()
    path = tmp_path_factory.mktemp("berlin2") / "berlin.yaml"
    path.write_text(yaml.safe_dump(doc))
    again = Session(load_deal(path, specs), specs)
    again.run()
    assert [(p.label, p.legs) for p in again.ledger.postings()] == \
        [(p.label, p.legs) for p in session.ledger.postings()]
    assert again.pools == session.pools
    assert again.metrics == session.metrics


# =============================================================================
# unit level: OP10's dials
# =============================================================================

POOL_DEAL = """
deal: unit-pool
horizon: 4
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [world, m1_savings, m2_savings, pool, elim]}
parties:
  - {name: operator-party, role: servicer}
instruments:
  - operator: op10_platform_aggregation
    pool_composition:
      pools:
        - id: p1
          pool_to: pool
          aggregate_from: elim
          members:
            - {id: m1, quantity: {baseline_bill: 1000.0, savings_fraction: 0.3},
               source: world, member_to: m1_savings}
            - {id: m2, quantity: 200.0, performance: [1.0, 0.5],
               source: world, member_to: m2_savings}
    origination_flow:
      entries:
        - {member: m1, entry: 0, terms: 4}
        - {member: m2, entry: 2, terms: 2}
"""


def _pool_session(tmp_path, specs, doc=POOL_DEAL, name="pool.yaml", swing=None):
    d = yaml.safe_load(doc if isinstance(doc, str) else yaml.safe_dump(doc))
    if swing:
        swing(d)
    p = tmp_path / name
    p.write_text(yaml.safe_dump(d))
    session = Session(load_deal(p, specs), specs)
    return session, session.run()


def test_op10_sub_ledgers_and_the_rollup(tmp_path, specs):
    """Each member's stream posts ONE consolidated entry: the sub-book leg
    (world -> member sub-ledger) and the pool-book leg (elimination ->
    pool); the member positions accumulate, the pool aggregates, the
    elimination mirrors — and the identity holds every period."""
    session, trace = _pool_session(tmp_path, specs)
    _green(trace)
    ledger = session.ledger
    assert [float(ledger.closing_balance("m1_savings", t)) for t in range(4)] == \
        [300.0, 600.0, 900.0, 1200.0]                  # 1000 x 0.3, every period
    assert [float(ledger.closing_balance("m2_savings", t)) for t in range(4)] == \
        [0.0, 0.0, 200.0, 300.0]                       # enters at 2; perf path 1, 0.5
    assert float(ledger.closing_balance("pool", 3)) == 1500.0   # 4x300 + 200 + 100
    assert ledger.closing_balance("elim", 3) == Fraction(-1500)
    assert len(ledger.postings(2)) == 2                # one entry per active member


def test_op10_a_foreign_posting_breaks_the_identity_loudly(tmp_path, specs):
    """A posting touching the pool account outside the operator (here via the
    M1 book hook, mid-run) breaks the composition identity — the run fails
    at that period's close, naming the broken side."""
    doc = yaml.safe_load(POOL_DEAL)
    p = tmp_path / "rogue.yaml"
    p.write_text(yaml.safe_dump(doc))
    session = Session(load_deal(p, specs), specs)

    def rogue(period, phase, ledger, sess):
        if period.index == 1 and phase == "accrue":
            ledger.post("rogue", 1, "accrue", payer="world", payee="pool", amount=999.0)

    with pytest.raises(LedgerError, match="composition identity"):
        session.run(book=rogue)


FLOOR_DEAL = """
deal: unit-floor
horizon: 4
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [world, m1_savings, m2_savings, pool, elim]}
parties:
  - {name: operator-party, role: servicer}
instruments:
  - operator: op10_platform_aggregation
    pool_composition:
      pools:
        - id: p1
          pool_to: pool
          aggregate_from: elim
          members:
            - {id: m1, quantity: 400.0, source: world, member_to: m1_savings}
            - {id: m2, quantity: 100.0, source: world, member_to: m2_savings}
    origination_flow:
      entries:
        - {member: m1, entry: 0, terms: 4}
        - {member: m2, entry: 2, terms: 2}
    thresholds:
      - {id: viability-floor, kind: origination_floor,
         measure: {account: pool, read: additions}, floor: 250.0}
"""


def test_op10_origination_floor_blocks_and_admits(tmp_path, specs):
    """The threshold as config: m2's entry decision at t=2 reads the pool's
    gross receipts at t=1 (400 >= 250 -> enters). Swung above the receipts
    (floor 401) the SAME config blocks the stint — recorded as skipped, the
    sub-ledger never books, the pool never receives."""
    session, trace = _pool_session(tmp_path, specs, FLOOR_DEAL)
    _green(trace)
    assert session.pools["p1"][2] == {"entered": ["m2"], "skipped": {}}
    assert float(session.ledger.closing_balance("pool", 3)) == 1800.0  # 4x400 + 2x100

    def raise_floor(doc):
        doc["instruments"][0]["thresholds"][0]["floor"] = 401.0

    blocked, trace = _pool_session(tmp_path, specs, FLOOR_DEAL, "floored.yaml",
                                   swing=raise_floor)
    _green(trace)
    assert blocked.pools["p1"][2] == {"entered": [],
                                      "skipped": {"m2": ["viability-floor"]}}
    assert blocked.ledger.closing_balance("m2_savings", 3) == 0
    assert float(blocked.ledger.closing_balance("pool", 3)) == 1600.0   # m1 only


def test_op10_first_period_entries_are_unconditional(tmp_path, specs):
    """t=0 entries carry no history to breach: a floor no measure could
    clear at t=0 (the pool's receipts only ever reach 400) still admits m1
    at t=0 — while the same floor blocks m2's t=2 entry (the t=1 receipts
    breach it)."""
    def uncrossable_floor(doc):
        doc["instruments"][0]["thresholds"][0]["floor"] = 401.0

    session, trace = _pool_session(tmp_path, specs, FLOOR_DEAL, name="tight.yaml",
                                   swing=uncrossable_floor)
    _green(trace)
    assert session.pools["p1"][0]["entered"] == ["m1"]        # unconditional: no
    assert session.pools["p1"][0]["skipped"] == {}             # history to breach
    assert session.pools["p1"][2]["skipped"] == {"m2": ["viability-floor"]}
    assert session.ledger.closing_balance("m2_savings", 3) == 0


def test_op10_terminal_distributes_or_retains(tmp_path, specs):
    """distribute_residual sweeps the named accounts' positive balances to
    the beneficiary at `at` (recorded; negative balances stay); retain books
    nothing — the balances stay."""
    def with_terminal(doc):
        doc["settings"]["op8_ledger"]["accounts"].append("beneficiary")
        doc["instruments"][0]["terminal_value_rule"] = {
            "kind": "distribute_residual", "accounts": ["pool", "elim"],
            "to": "beneficiary", "at": 3}

    session, trace = _pool_session(tmp_path, specs, swing=with_terminal)
    _green(trace)
    ledger = session.ledger
    swept = session.pools["p1"]["terminal"]["swept"]
    assert swept == {"pool": Fraction(1500)}          # elim's balance is negative
    assert ledger.closing_balance("beneficiary", 3) == Fraction(1500)
    assert ledger.closing_balance("pool", 3) == 0
    assert ledger.closing_balance("elim", 3) == Fraction(-1500)

    def retain(doc):
        doc["settings"]["op8_ledger"]["accounts"].append("beneficiary")
        doc["instruments"][0]["terminal_value_rule"] = {"kind": "retain"}

    session, trace = _pool_session(tmp_path, specs, name="retain.yaml", swing=retain)
    _green(trace)
    assert session.pools["p1"]["terminal"] == {"kind": "retain", "at": 3}
    assert len(session.ledger.postings(3)) == 2        # the two member streams only


def test_op10_refuses_malformed_configs(tmp_path, specs):
    base = yaml.safe_load(POOL_DEAL)
    op10 = lambda d: d["instruments"][0]               # noqa: E731
    cases = [
        {"pool_composition": ["a", "b"]},                          # wrong shape
        {"pool_composition": {"pools": []}},                       # no pools
        {"pool_composition": {"pools": [{"id": "p1",
                                         "members": op10(base)["pool_composition"]["pools"][0]["members"]}]}},
                                                                    # pool_to/elim missing
        {"origination_flow": None},                                # no schedule
        {"origination_flow": {"entries": [{"member": "ghost", "entry": 0, "terms": 1}]}},
        {"origination_flow": {"entries": [{"member": "m1", "entry": 0, "terms": 0}]}},
        {"origination_flow": {"entries": [{"member": "m2", "entry": 3, "terms": 4}]}},
                                                                    # beyond the clock
        {"thresholds": [{"id": "t", "kind": "exit_below", "measure": {"account": "pool",
                                                                     "read": "flow"},
                          "floor": 1.0}]},                          # unknown kind
        {"thresholds": [{"id": "t", "kind": "origination_floor",
                         "measure": {"account": "pool", "read": "mid"}, "floor": 1.0}]},
        {"thresholds": [{"id": "t", "kind": "origination_floor",
                         "measure": {"account": "undeclared", "read": "flow"},
                         "floor": 1.0}]},
        {"terminal_value_rule": {"kind": "distribute_residual", "at": 1}},   # no to/accounts
        {"terminal_value_rule": {"kind": "liquidate"}},            # unknown kind
        {"terminal_value_rule": {"kind": "distribute_residual", "to": "world",
                                 "accounts": ["pool"], "at": 9}},  # beyond the clock
    ]
    for patch in cases:
        doc = yaml.safe_load(yaml.safe_dump(base))
        op10(doc).update(patch)
        p = tmp_path / "bad.yaml"
        p.write_text(yaml.safe_dump(doc))
        with pytest.raises(DealConfigError):
            Session(load_deal(p, specs), specs)


def test_op10_two_pools_keep_separate_books(tmp_path, specs):
    """Genericity: several pools under one composition, each with its own
    pool book, elimination account and identity; account reuse across pools
    is refused."""
    doc = yaml.safe_load(POOL_DEAL)
    pools = doc["instruments"][0]["pool_composition"]["pools"]
    pools[0]["members"] = [pools[0]["members"][0]]         # m1 only
    doc["settings"]["op8_ledger"]["accounts"] += ["pool2", "elim2", "m3_savings"]
    pools.append({"id": "p2", "pool_to": "pool2", "aggregate_from": "elim2",
                  "members": [{"id": "m3", "quantity": 50.0, "source": "world",
                               "member_to": "m3_savings"}]})
    flow = doc["instruments"][0]["origination_flow"]
    flow["entries"] = [e for e in flow["entries"] if e["member"] == "m1"]
    flow["entries"].append({"member": "m3", "entry": 1, "terms": 3})
    session, trace = _pool_session(tmp_path, specs, doc)
    _green(trace)
    ledger = session.ledger
    assert float(ledger.closing_balance("pool", 3)) == 1200.0        # p1: 4 x 300
    assert float(ledger.closing_balance("pool2", 3)) == 150.0        # p2: 3 x 50
    assert set(session.pools) == {"p1", "p2"}

    def share_book(d):                                        # refused: shared pool book
        d["instruments"][0]["pool_composition"]["pools"][1]["pool_to"] = "pool"
        d["instruments"][0]["pool_composition"]["pools"][1]["aggregate_from"] = "elim"

    with pytest.raises(DealConfigError, match="already serves"):
        _pool_session(tmp_path, specs, doc, "shared.yaml", swing=share_book)


# =============================================================================
# the birth-certificate statement, extended to M5
# =============================================================================

def test_the_engine_names_no_case_m5():
    """Config-only reproduction, M5 evidence: the engine package and the
    operator spec contain no case name or case vocabulary of the Berlin
    oracle (as words) — the fixture is a seeded test-side generator
    producing a YAML config through the standard loader, every path and the
    calibration are data, and no engine behavior is keyed to a case."""
    def words(text, *needles):
        return [n for n in needles if re.search(rf"\b{re.escape(n)}\b", text)]

    engine = "\n".join(p.read_text() for p in (KIT / "oe" / "engine").glob("*.py")).lower()
    assert not words(engine, "berlin", "citynvest", "energieagentur",
                     "energiesparpartnerschaft", "wwf")
    spec_text = (KIT / "operators.yaml").read_text().lower()
    assert not words(spec_text, "berlin", "citynvest", "energieagentur",
                     "energiesparpartnerschaft", "wwf")
