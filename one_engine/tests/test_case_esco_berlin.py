"""Reproduction test: esco-product's Berlin pool (1,400 buildings in lots, a pool reserve, a terminal sweep), run on one_engine.

Where the numbers come from
---------------------------
esco-product (``engine/tests/test_m5.py``, vendored at
``cases/reference/esco_engine/test_m5.py``) reproduces the Berlin Energy
Saving Partnership as structure and scale: a seeded generator (seed 1996)
draws 1,400 buildings' baseline bills (lognormal, median 35,000, sigma 0.45)
and savings fractions (Normal 25.5%, 2%, clipped to 10-45%), scales the
stock so the guaranteed savings are the published EUR 12.5m a cycle, and
deals the buildings into 46 lots that enter over five years (10/10/10/10/6
lots). The pilot lot runs an 8-year cycle and rolls over into a second one.
The platform's fee is 78% of what the pool collects and the public client's
net 22%, the pair implied by the published 12.5m savings and 2.7-2.8m net.
A 500,000 pool reserve is topped up before the claims and released to the
client in the final year.

The generator is rebuilt here test-side, as esco-product does (it is test
data, not engine code), and the deal is written as a one_engine case:

* ``pool`` streams each building's guaranteed savings (bill x fraction, each
  read at 6 decimals as esco-product's loader does) into ``platform_pool``
  through its own sub-ledger, one stint per cycle (the pilot lot enters
  twice);
* ``claim`` measures the fee and the client's net on what the pool
  collected in the period;
* ``stack`` sweeps the pool into the waterfall account and pays, in rank,
  the reserve up to its target, the fee, then the client. The client's net
  is the variable level: what it is not paid (the year-0 top-up) is
  extinguished against the claims book, as esco-product's OP3 extinguishes
  every accrual it settles;
* a second ``stack`` at the final close sweeps the reserve to the client
  (esco-product's ``distribute_residual``).

Two referees:

1. **esco-product's own engine, account for account.** The esco run's every
   closing balance in every period (``cases/reference/esco_engine/
   berlin.json``, hash-pinned, written by
   ``consolidation/experiments/capture_esco_berlin.py``) must be equal here,
   as a fraction. esco-product posts each building's stream as one entry
   of four legs: the building stock pays the member's sub-ledger, and an
   elimination account pays the pool. one_engine's pool posts the same
   stream as pool <- sub-ledger, so esco-product's two mirror accounts
   (``building_stock_bills`` and ``platform_consolidation``) have no
   counterpart; each is held equal to minus the sum of the 1,400
   sub-ledgers, which is what esco-product's composition identity says
   they are.
2. **The published figures and esco-product's own assertions** of
   ``test_m5.py`` on this fixture, repeated below.

Not exercised: a pool shortfall against the fee. esco-product's fixed level
is then paid in full and the client pays the gap into the pool; the stack
pays the fee what is there. In this deal the pool always covers the fee.

If this fails, the engine or the configuration is wrong, never the number.
"""
import json
import math
import pathlib
import random
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.oracles import verify

REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"

# ── the seeded generator (esco-product test_m5.py, test-side data) ───────────
SEED = 1996
N_BUILDINGS, N_LOTS = 1400, 46
BILL_MEDIAN, BILL_SIGMA = 35000.0, 0.45
CUT_MEAN, CUT_SD = 0.255, 0.02
SAVINGS_TARGET = 12_500_000.0
HORIZON = 15
FIRST_TERMS, ROLLOVER_AT = 8, 8
RESERVE_TARGET = 500_000
CLIENT_SHARE = F(11, 50)            # 2.75m / 12.5m, from the published pair
FEE_SHARE = 1 - CLIENT_SHARE        # 39/50, the implied fee (derived)
MIRRORS = ("building_stock_bills", "platform_consolidation")


def q6(x):
    """A float read at 6 decimals, as esco-product's loader books it."""
    return F(str(round(x, 6)))


def berlin_stock():
    """esco-product's ``_berlin_config`` generator: (lots, booked savings per building, stints per lot)."""
    rng = random.Random(SEED)
    bills = [rng.lognormvariate(math.log(BILL_MEDIAN), BILL_SIGMA) for _ in range(N_BUILDINGS)]
    cuts = [min(max(rng.gauss(CUT_MEAN, CUT_SD), 0.10), 0.45) for _ in range(N_BUILDINGS)]
    # the calibration: scale the stock so the booked quantities sum to 12.5m within half a cent
    scale = SAVINGS_TARGET / sum(b * c for b, c in zip(bills, cuts))
    for _ in range(8):
        booked_total = sum(q6(b * scale) * q6(c) for b, c in zip(bills, cuts))
        if abs(float(booked_total) - SAVINGS_TARGET) < 0.005:
            break
        scale *= SAVINGS_TARGET / float(booked_total)
    order = list(range(N_BUILDINGS))
    rng.shuffle(order)
    sizes = [N_BUILDINGS // N_LOTS + (1 if j < N_BUILDINGS % N_LOTS else 0) for j in range(N_LOTS)]
    lots, at = [], 0
    for size in sizes:
        lots.append(order[at:at + size])
        at += size
    booked = {i: q6(bills[i] * scale) * q6(cuts[i]) for i in range(N_BUILDINGS)}
    stints = [[(0, FIRST_TERMS), (ROLLOVER_AT, HORIZON - ROLLOVER_AT)] if j == 0 else [(j // 10, HORIZON - j // 10)]
              for j in range(N_LOTS)]
    return lots, booked, stints, (bills, cuts, scale)


def bldg(i):
    return f"bldg-{i:04d}"


def berlin_case():
    lots, booked, stints, _ = berlin_stock()
    members, entries = {}, []
    for j, lot in enumerate(lots):
        for i in lot:
            members[bldg(i)] = {"quantity": booked[i], "account": f"{bldg(i)}_savings"}
            entries += [{"member": bldg(i), "entry": e, "terms": n} for e, n in stints[j]]
    natures = {"platform_pool": "asset", "platform_operations": "equity", "services_fee_claim": "liability",
               "client_net_claim": "liability", "services_cash": "asset", "client_budget": "asset",
               "pool_reserve": "asset", "waterfall_availability": "asset"}
    natures.update({f"{bldg(i)}_savings": "income" for i in range(N_BUILDINGS)})
    measured = {"account": "platform_pool", "read": "flow"}
    return {
        "case": "esco-berlin-pool", "periods": HORIZON, "phases": ["open", "accrue", "settle", "close"],
        "currency": "EUR", "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "opening": {"deal": {a: {"nature": n, "value": 0} for a, n in natures.items()}},
        "rulebooks": {
            "pool": {"kind": "pool", "entity": "deal", "phase": "accrue", "pool_to": "platform_pool",
                     "members": members, "entries": entries, "thresholds": []},
            "claims": {"kind": "claim", "entity": "deal", "phase": "settle", "claims": {
                "fee": {"claim": "services_fee_claim", "against": "platform_operations", "share": FEE_SHARE,
                        "of": measured},
                "client": {"claim": "client_net_claim", "against": "platform_operations", "share": CLIENT_SHARE,
                           "of": measured}}},
            "waterfall": {"kind": "stack", "entity": "deal", "phase": "settle", "pool": "waterfall_availability",
                          "claims_book": "platform_operations", "sweep": ["platform_pool"],
                          "extinguished": "platform_operations",
                          "tiers": [{"name": "reserve", "target": RESERVE_TARGET, "to": "pool_reserve"},
                                    {"name": "fee", "claim": "services_fee_claim", "to": "services_cash",
                                     "shortfall": "carry"},
                                    {"name": "client", "claim": "client_net_claim", "to": "client_budget",
                                     "shortfall": "extinguish"}]},
            "terminal": {"kind": "stack", "entity": "deal", "phase": "close", "from": HORIZON - 1,
                         "until": HORIZON - 1, "pool": "pool_reserve", "claims_book": "platform_operations",
                         "tiers": [], "residual": "client_budget"}},
    }


@pytest.fixture(scope="module")
def berlin():
    res = run_case(berlin_case())
    assert res.failed_checks() == []
    return res


@pytest.fixture(scope="module")
def book(berlin):
    return berlin.group.books["deal"]


def flow(book, acct, t):
    return book.balance(acct, t) - book.opening(acct, t)


def receipts(book, t):
    """The pool's gross receipts in period t: what the pool rulebook posted into ``platform_pool``."""
    return sum((p.amount for p in book.postings if p.period == t and p.debit == "platform_pool"
                and p.event.startswith("pool.")), F(0))


def members_total(book, t):
    return sum((book.balance(f"{bldg(i)}_savings", t) for i in range(N_BUILDINGS)), F(0))


# ── 1. esco-product's own engine, every account, every period ───────────────

def test_every_balance_equals_esco_products_engine(book):
    rel = "esco_engine/berlin.json"
    verify(rel)
    ref = json.loads((REFERENCE / rel).read_text())
    assert book.periods == ref["periods"] == HORIZON
    for acct, values in ref["closing"].items():
        want = [F(v) for v in values]
        if acct in MIRRORS:
            assert [-members_total(book, t) for t in range(HORIZON)] == want, acct
        else:
            assert [book.balance(acct, t) for t in range(HORIZON)] == want, acct


def test_the_entry_record_equals_esco_products(berlin):
    ref = json.loads((REFERENCE / "esco_engine" / "berlin.json").read_text())
    rec = berlin.records("pool")
    got = {str(t): rec[t]["entered"] for t in range(HORIZON) if rec[t]["entered"]}
    assert got == ref["entered"]
    assert all(not rec[t]["skipped"] for t in range(HORIZON))
    assert berlin.records("terminal")[HORIZON - 1]["residual"] == F(ref["terminal_swept"]["pool_reserve"])


# ── 2. the published figures and test_m5.py's assertions ────────────────────

def test_aggregate_guaranteed_savings_per_cycle(book):
    full = [receipts(book, t) for t in range(4, HORIZON)]
    assert all(a == full[0] for a in full)                       # static stock once every lot is in
    assert float(full[0]) == pytest.approx(12_500_000.0, abs=0.01)
    _, _, _, (bills, cuts, scale) = berlin_stock()
    assert sum(b * scale * c for b, c in zip(bills, cuts)) / (sum(bills) * scale) == pytest.approx(0.255, abs=0.005)


def test_the_implied_fee_falls_out_and_nothing_is_created(book):
    for t in range(4, HORIZON - 1):
        fee, net = flow(book, "services_cash", t), flow(book, "client_budget", t)
        assert float(fee) == pytest.approx(9_750_000.0, abs=0.01)
        assert 2_700_000.0 <= float(net) <= 2_800_000.0
    for t in range(HORIZON):                                      # fee + net + reserve change == receipts, exactly
        assert flow(book, "services_cash", t) + flow(book, "client_budget", t) + flow(book, "pool_reserve", t) \
            == receipts(book, t), t


def test_the_pool_is_the_sum_of_the_sub_ledgers_across_the_rollover(book):
    lots, booked, _, _ = berlin_stock()
    for t in (0, 4, 7, 8, HORIZON - 1):
        assert members_total(book, t) == sum((receipts(book, s) for s in range(t + 1)), F(0)), t
    pilot, g = f"{bldg(lots[0][0])}_savings", booked[lots[0][0]]
    assert book.balance(pilot, 7) == 8 * g                       # first cycle: 8 periods
    assert book.balance(pilot, 8) == 9 * g                       # rollover: no gap
    assert book.balance(pilot, 14) == 15 * g
    assert flow(book, pilot, 7) == g and flow(book, pilot, 8) == g


def test_origination_and_the_mid_run_rollover(berlin, book):
    lots, _, _, _ = berlin_stock()
    rec = berlin.records("pool")
    assert {t: len(rec[t]["entered"]) for t in range(HORIZON) if rec[t]["entered"]} == \
        {0: 310, 1: 310, 2: 300, 3: 300, 4: 180, 8: 31}
    pilot = {bldg(i) for i in lots[0]}
    assert pilot <= set(rec[0]["entered"]) and pilot <= set(rec[8]["entered"])
    adds = [receipts(book, t) for t in range(HORIZON)]
    assert adds[0] < adds[4] and adds[3] < adds[4] and adds[5] == adds[14]


def test_reserve_topped_first_never_funded_by_the_client_swept_at_the_end(book):
    f0 = receipts(book, 0)
    for t in range(HORIZON - 1):
        assert book.balance("pool_reserve", t) == RESERVE_TARGET, t
    assert flow(book, "client_budget", 0) == CLIENT_SHARE * f0 - RESERVE_TARGET > 0
    assert all(flow(book, "client_budget", t) >= 0 for t in range(HORIZON))
    assert book.balance("pool_reserve", HORIZON - 1) == 0
    assert flow(book, "client_budget", HORIZON - 1) == CLIENT_SHARE * receipts(book, HORIZON - 1) + RESERVE_TARGET


def test_lifetime_totals(book):
    total = sum((receipts(book, t) for t in range(HORIZON)), F(0))
    assert book.balance("services_cash", HORIZON - 1) == FEE_SHARE * total
    assert book.balance("client_budget", HORIZON - 1) == CLIENT_SHARE * total
    for a in ("platform_pool", "waterfall_availability", "services_fee_claim", "client_net_claim",
              "platform_operations"):
        assert all(book.balance(a, t) == 0 for t in range(HORIZON)), a


# ── the two pack rules this case needed ──────────────────────────────────────

def _mini(entries, tiers=None):
    """Two members (100 and 10 a period) into one pool over 4 periods; optionally a stack over the pool."""
    natures = {"pool": "asset", "m1": "income", "m2": "income", "book": "equity", "reserve": "asset",
               "fee_claim": "liability", "fee_cash": "asset", "out": "asset"}
    doc = {
        "case": "unit-pool", "periods": 4, "phases": ["open", "accrue", "settle", "close"], "currency": "LCU",
        "floors": {}, "generators": {}, "contracts": {}, "schemes": {}, "cuts": {}, "measures": {},
        "opening": {"deal": {a: {"nature": n, "value": 0} for a, n in natures.items()}},
        "rulebooks": {"pool": {
            "kind": "pool", "entity": "deal", "phase": "accrue", "pool_to": "pool",
            "members": {"m1": {"quantity": 100, "performance": [1, 1, F(1, 2), F(1, 4)], "account": "m1"},
                        "m2": {"quantity": 10, "account": "m2"}},
            "entries": entries,
            "thresholds": [{"id": "floor", "kind": "origination_floor", "floor": 50, "if_missing": "pass",
                            "measure": [100, 0, 100, 100]}]}},
    }
    if tiers is not None:
        doc["rulebooks"]["fee"] = {"kind": "claim", "entity": "deal", "phase": "settle", "claims": {
            "fee": {"claim": "fee_claim", "against": "book", "amount": 30}}}
        doc["rulebooks"]["stack"] = {"kind": "stack", "entity": "deal", "phase": "settle", "pool": "pool",
                                     "claims_book": "book", "tiers": tiers, "residual": "out"}
    return doc


def test_a_member_rolls_over_into_a_second_stint_gated_on_its_own():
    # m1 runs 0..1 and tries to re-enter at 2 with m2: both entries read the floor measure at 1 (0 < 50) and
    # are blocked. Re-entering at 3 instead reads 100 and passes.
    res = run_case(_mini([{"member": "m1", "entry": 0, "terms": 2}, {"member": "m1", "entry": 2, "terms": 2},
                          {"member": "m2", "entry": 2, "terms": 2}]))
    assert res.failed_checks() == []
    book = res.group.books["deal"]
    assert [flow(book, "m1", t) for t in range(4)] == [100, 100, 0, 0]
    assert [res.records("pool")[t]["skipped"] for t in range(4)] == [{}, {}, {"m1": ["floor"], "m2": ["floor"]}, {}]
    res = run_case(_mini([{"member": "m1", "entry": 0, "terms": 2}, {"member": "m1", "entry": 3, "terms": 1}]))
    book = res.group.books["deal"]
    # the second stint reads its performance from its own start (1), not the member's first entry (1/4)
    assert [flow(book, "m1", t) for t in range(4)] == [100, 100, 0, 100]
    assert [res.records("pool")[t]["entered"] for t in range(4)] == [["m1"], [], [], ["m1"]]


def test_a_member_is_never_in_two_stints_at_once():
    from one_engine.inputs import InputError
    with pytest.raises(InputError, match="overlapping stints 0..2 and 2..3"):
        run_case(_mini([{"member": "m1", "entry": 0, "terms": 3}, {"member": "m1", "entry": 2, "terms": 2}]))


def test_a_reserve_tier_is_topped_to_target_in_rank_then_due_nothing():
    res = run_case(_mini([{"member": "m1", "entry": 0, "terms": 4}],
                         tiers=[{"name": "reserve", "target": 120, "to": "reserve"},
                                {"name": "fee", "claim": "fee_claim", "to": "fee_cash", "shortfall": "carry"}]))
    assert res.failed_checks() == []
    book = res.group.books["deal"]
    # receipts 100, 100, 50, 25: the reserve takes 100 then 20 and nothing after; the fee (30 a period) is
    # paid what is left, its arrears carried; the residual holder takes the rest
    assert [book.balance("reserve", t) for t in range(4)] == [100, 120, 120, 120]
    assert [flow(book, "fee_cash", t) for t in range(4)] == [0, 60, 30, 25]
    assert [book.balance("fee_claim", t) for t in range(4)] == [30, 0, 0, 5]
    assert [flow(book, "out", t) for t in range(4)] == [0, 20, 20, 0]


def test_a_reserve_target_is_never_negative():
    from one_engine.inputs import InputError
    with pytest.raises(InputError, match="reserve target is never negative"):
        run_case(_mini([{"member": "m1", "entry": 0, "terms": 4}],
                       tiers=[{"name": "reserve", "target": -1, "to": "reserve"}]))
