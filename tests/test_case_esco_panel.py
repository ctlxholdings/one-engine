"""Reproduction test: esco-product's failure panel (ten worlds), run on one_engine.

The panel (esco-product ``docs/PANEL.md``; numbers from
``engine/tests/test_m6.py``) is a set of switches. Each one snaps onto a
standing deal and breaks it in one place, and the books stay whole while it
happens: the break is real money moving, not a bug. Here the standing deals
are one_engine cases (``cases/panel/*.yaml``, and ERIA's own
``cases/eria.yaml``), the switches are overlay files
(``cases/panel/worlds/*.yaml``), and the mechanisms they flip are the
obligations pack (:mod:`one_engine.packs.obligations`).

The composer (test-side; the engine sees only a plain case)
-------------------------------------------------------------
A world file carries::

    world, family, what, watch   description (``watch`` is what to look at)
    set:               {dotted.path: value}  replace a value the base HAS
    set_where_present: {dotted.path: value}  replace it only where the base has it
                                             (W2 binds the panel base or ERIA)
    add:               {dotted.path: {key: value}}  new entries under a mapping
                                             (created if absent); an existing key refuses

A world that binds nothing refuses, and so does a ``set`` the base has no
surface for. The composed document is an ordinary case and goes through
the runner's unread-key refusal like any other.

Reading the books against the panel
-----------------------------------
Each panel deal is one book ("deal"; ERIA's is "esco"). The panel's accounts
keep their names. The panel's ``flow`` of an account is its natural movement
in the period. Its ``corkscrew`` is (opening, additions, deductions,
closing), additions and deductions being the postings that raise and lower
the account by its nature. Where a credit-natured account stands for a party
receiving money (ERIA's share capital returned, the owner's residual), the
sign is turned explicitly where it is read.

"Green" is what esco-product calls the identities: every integrity check on
every book, every rulebook check (the pools' composition), and the journal
equal to the balances (``test_chain.journal_is_ledger``).

Where the books differ from the panel
-------------------------------------
Every panel number is asserted. Where one_engine's books legitimately give
another number, the panel's assertion is kept, marked ``xfail`` (strict)
with the reason, and one_engine's own number is asserted beside it. On this
panel that happens only on ERIA, in two places:

1. W2 on ERIA. ERIA's shared-savings contract pays the provider ``split`` of
   the savings *measured*: 0.8 x performance x q. The panel's operator
   serves the provider's 80% *first* and caps it by what was measured:
   min(0.8, performance) x q. These agree while performance is 1 and
   differ from the third year of the ramp.
2. W5 on ERIA. one_engine's ERIA books the 850,000 the owner pays in at
   period 0 (``equity_funding``). So the estate swept at realization is the
   ESCO's cash, 2,129,292.0576. Recovering the capital returns that share
   capital, and the owner keeps a residual of +579,292.0576. The panel's
   ERIA fixture never books the 850,000 paid in, so its estate is 850,000
   lower, and it still recovers the 850,000 out of that estate. That is
   the call of -270,707.9424 its owner funds.

The combined W2 + W5 stress inherits both differences. Its mechanism holds
to the cent: the whole revenue loss reappears one for one in the owner's
position. Its two numbers do not (the revenue loss, and -603,409.83).
"""
import copy
import pathlib
import re
from fractions import Fraction as F

import pytest

from one_engine.cases import RULEBOOKS, run_case
from one_engine.inputs import load_yaml_strict
from one_engine.tests.test_chain import journal_is_ledger

CASES = pathlib.Path(__file__).resolve().parents[1] / "cases"
PANEL = CASES / "panel"
WORLDS = PANEL / "worlds"
ERIA = CASES / "eria.yaml"

WORLD_KEYS = {"world", "family", "what", "watch", "set", "set_where_present", "add"}


# ── the composer ─────────────────────────────────────────────────────────────

def read(path):
    return load_yaml_strict(pathlib.Path(path).read_text())


def _parent(doc, dotted, world):
    """The mapping holding the last segment of ``dotted``, or None where the path does not exist."""
    *parents, leaf = dotted.split(".")
    node = doc
    for k in parents:
        if not isinstance(node, dict) or k not in node:
            return None, leaf
        node = node[k]
    return (node if isinstance(node, dict) else None), leaf


def compose(base, *worlds):
    """Layer world overlays onto a base case (pure data in, pure data out); see the module docstring."""
    doc = copy.deepcopy(base)
    for w in worlds:
        name = w.get("world")
        unknown = set(w) - WORLD_KEYS
        if unknown:
            raise ValueError(f"world {name!r}: unknown keys {sorted(unknown)}")
        bound = 0
        for dotted, value in (w.get("set") or {}).items():
            node, leaf = _parent(doc, dotted, name)
            if node is None or leaf not in node:
                raise ValueError(f"world {name!r}: the base has no {dotted} to set")
            node[leaf] = copy.deepcopy(value)
            bound += 1
        for dotted, value in (w.get("set_where_present") or {}).items():
            node, leaf = _parent(doc, dotted, name)
            if node is not None and leaf in node:
                node[leaf] = copy.deepcopy(value)
                bound += 1
        for dotted, entries in (w.get("add") or {}).items():
            node, leaf = _parent(doc, dotted, name)
            if node is None:
                raise ValueError(f"world {name!r}: the base has nowhere to add {dotted}")
            target = node.setdefault(leaf, {})
            for key, value in entries.items():
                if key in target:
                    raise ValueError(f"world {name!r}: {dotted}.{key} is already in the base")
                target[key] = copy.deepcopy(value)
                bound += 1
        if not bound:
            raise ValueError(f"world {name!r} binds nothing on this base")
    return doc


def base(name):
    return read(ERIA if name == "eria" else PANEL / f"{name}.yaml")


def world(name):
    return read(WORLDS / f"{name}.yaml")


def run(base_name, *world_names, edit=None):
    doc = compose(base(base_name), *(world(w) for w in world_names))
    if edit:
        edit(doc)
    res = run_case(doc)
    green(res)
    return res


def green(res):
    """esco-product's identities: integrity, rulebook checks, and the journal equal to the ledger."""
    assert res.failed_checks() == []
    for entity, book in res.group.books.items():
        assert journal_is_ledger(book), entity


# ── reading the books ────────────────────────────────────────────────────────

def book(res):
    (b,) = [b for e, b in res.group.books.items() if e in ("deal", "esco")]
    return b


def flow(res, acct, t):
    b = book(res)
    return b.balance(acct, t) - b.opening(acct, t)


def closing(res, acct, t):
    return book(res).balance(acct, t)


def corkscrew(res, acct, t):
    """(opening, additions, deductions, closing): the postings that raise and lower ``acct`` by its nature."""
    b = book(res)
    moves = [p.effect[acct] * p.amount for p in b.postings if p.period == t and acct in (p.debit, p.credit)]
    return (b.opening(acct, t), sum((m for m in moves if m > 0), F(0)), -sum((m for m in moves if m < 0), F(0)),
            b.balance(acct, t))


def events(res, t):
    return [p.event for p in book(res).postings if p.period == t]


# ── the bases stand on their own ─────────────────────────────────────────────

@pytest.mark.parametrize("name", ["panel_base", "panel_pool_base", "panel_supply_base", "metronet"])
def test_the_base_deals_stand(name):
    green(run_case(base(name)))


def test_panel_base_steady_state():
    res = run_case(base("panel_base"))
    green(res)
    for t in range(10):
        assert flow(res, "verified_savings", t) == 120000
        assert corkscrew(res, "collections_pledged", t)[1] == 84000
        assert flow(res, "senior_funder", t) == 63000
        assert flow(res, "platform_pocket", t) == 21000 - 12000
    assert res.records("collections_route")[0]["satisfied"] is True


def test_panel_pool_base_steady_state():
    res = run_case(base("panel_pool_base"))
    green(res)
    for t in range(10):
        assert corkscrew(res, "collections_pledged", t)[1] == 90000
        assert flow(res, "platform_pocket", t) == 54000 - 45000
        assert flow(res, "hosts_collective", t) == 36000
    assert res.records("pool")[0]["entered"] == [f"host_{h}" for h in "abcdef"]


def test_the_metronet_chassis_books_nothing_before_a_failure():
    res = run_case(base("metronet"))
    green(res)
    assert book(res).postings == ()
    assert {r["status"] for r in res.records("servicing").values()} == {"operative"}


# ── W1: servicer insolvency, the Metronet reproduction ───────────────────────

PUBLISHED_PUT = 1747          # PANEL.md: the published ~1,747 the panel is held to, +-10%


@pytest.fixture(scope="module")
def w1():
    return run("metronet", "w1_servicer_insolvency")


def test_w1_metronet_identities_and_sequence(w1):
    # the mechanism in the failure week, in firing order: fail -> put -> par -> wipe -> in-house
    servicing = w1.records("servicing")
    assert servicing[3]["status"] == "operative" and servicing[4]["status"] == "suspended"
    assert w1.records("lender_put")[4]["exercised"] is True
    ev = events(w1, 4)
    put = ev.index("lender_put.par:4")
    settle = ev.index("settlement.guaranteed.paid:4")
    admin = ev.index("in_house.administration:4")
    assert put < settle < admin
    p = book(w1).postings[[i for i, x in enumerate(book(w1).postings) if x.event == "lender_put.par:4"][0]]
    assert (p.credit, p.amount, p.effect["tfl_account"]) == ("tfl_account", 1810, -1)


def test_w1_metronet_put_settlement_vs_published(w1):
    """EXPECTED = PUBLISHED: the put settlement ~1,747, within the pre-registered +-10%."""
    settlement = -flow(w1, "tfl_account", 4) - 630 + 308
    assert settlement == 1810 == w1.records("lender_put")[4]["par"]
    assert abs(settlement - PUBLISHED_PUT) <= F(1, 10) * PUBLISHED_PUT
    print(f"\nW1 put settlement: computed {float(settlement):,.1f} vs published ~{PUBLISHED_PUT:,} "
          f"({100 * (float(settlement) / PUBLISHED_PUT - 1):+.1f}%, band +-10%)")


def test_w1_metronet_losses_exactly_540_lenders_at_par(w1):
    # lenders at par: the guaranteed claim repaid in full
    assert flow(w1, "guaranteed_lenders", 4) == 1810
    assert closing(w1, "guaranteed_claim", 4) == 0
    # the wipe: the tail and the equity extinguish UNPAID; the private losses are exactly 540
    assert flow(w1, "tail_lenders", 4) == 0 and flow(w1, "equity_holders", 4) == 0
    losses = (190 - flow(w1, "tail_lenders", 4)) + (350 - flow(w1, "equity_holders", 4))
    assert losses == 540 == closing(w1, "claims_extinguished", 11)
    assert w1.records("settlement")[4]["extinguished"] == {"tail": 190, "equity": 350}
    for t in (4, 11):                    # crystallized once: nothing is recognised again after the failure
        assert closing(w1, "tail_claim", t) == 0 and closing(w1, "equity_claim", t) == 0


def test_w1_metronet_in_house_cost_path(w1):
    # one-off: put 1,810 + admin 630 - grants 308 = -2,132 in the failure week
    assert closing(w1, "tfl_account", 4) == -2132
    assert flow(w1, "gov_account", 4) == -308
    assert flow(w1, "in_house_ops", 4) == 630
    for t in range(5, 12):
        assert flow(w1, "in_house_ops", t) == 14
        assert closing(w1, "tfl_account", t) == -2132 - 14 * (t - 4)
    assert closing(w1, "in_house_ops", 11) == 630 + 7 * 14


# ── W1b: diversion and the G1 guard ──────────────────────────────────────────

def _no_guard(doc):
    del doc["rulebooks"]["g1_guard"]


def test_w1b_diversion_visible_guard_makes_nets_whole():
    res = run("panel_base", "w1b_diversion")
    assert corkscrew(res, "operator_own", 4) == (0, 84000, 84000, 0)
    b = book(res)
    into_pledged = [p for p in b.postings if p.period == 4 and "collections_pledged" in (p.debit, p.credit)
                    and p.effect["collections_pledged"] > 0]
    assert len(into_pledged) == 1 and into_pledged[0].event.startswith("g1_guard.remedy")
    rec = res.records("g1_guard")[4]
    assert rec["violated"] is True and rec["remedy_paid"] == 84000
    assert flow(res, "senior_funder", 4) == 63000
    assert flow(res, "platform_pocket", 4) == 9000
    assert closing(res, "senior_funder", 9) == 630000
    assert closing(res, "operator_own", 9) == 0


def test_w1b_guard_off_the_diversion_sticks():
    res = run("panel_base", "w1b_diversion", edit=_no_guard)
    assert flow(res, "collections_pledged", 4) == 0
    assert flow(res, "senior_funder", 4) == 0
    assert closing(res, "operator_own", 4) == 84000
    assert closing(res, "senior_funder", 9) == 63000 * 9
    assert res.records("collections_route")[3]["satisfied"] is True


# ── W2: asset underperformance ───────────────────────────────────────────────

RAMP = [F(s) for s in ("1.0", "0.95", "0.9", "0.85", "0.8", "0.75", "0.7", "0.65", "0.6", "0.55")]
Q = F(3080573) * F("0.108")                                       # ERIA's measured savings a year
ERIA_RAMP = [F(1), F(1), F("0.9"), F("0.8"), F("0.7"), F("0.6"), F("0.5"), F("0.4")]


def test_w2_underperformance_panel():
    res = run("panel_base", "w2_underperformance")
    for t, r in enumerate(RAMP):
        assert flow(res, "verified_savings", t) == 120000 * r
        assert corkscrew(res, "collections_pledged", t)[1] == 84000 * r
        assert flow(res, "senior_funder", t) == 63000 * r
        assert flow(res, "platform_pocket", t) == 21000 * r - 12000
    assert flow(res, "platform_pocket", 0) > 0 and flow(res, "platform_pocket", 9) < 0
    assert "forecast_bias" in (WORLDS / "w2_underperformance.yaml").read_text().lower()


@pytest.fixture(scope="module")
def eria_w2():
    return run("eria", "w2_underperformance")


def test_w2_on_eria_the_provider_takes_its_share_of_what_was_measured(eria_w2):
    """one_engine's ERIA: the fee is split x the savings measured, so it rides the whole ramp."""
    for i, r in enumerate(ERIA_RAMP):
        assert flow(eria_w2, "cash", i + 1) == F("0.8") * r * Q


@pytest.mark.xfail(strict=True, reason=(
    "ERIA's shared_savings pays the provider split x measured savings (0.8 x perf x q); the panel's OP2 "
    "serves the provider's 80% first, capped by what was measured (min(0.8, perf) x q). Equal while perf = 1, "
    "different from the ramp's 0.9"))
def test_w2_on_eria_panel_number_provider_first(eria_w2):
    for i, r in enumerate(ERIA_RAMP):
        assert flow(eria_w2, "cash", i + 1) == min(F("0.8"), r) * Q


# ── W3: single-client concentration ─────────────────────────────────────────

def test_w3_single_client_collapse():
    res = run("panel_pool_base", "w3_single_client")          # green includes the pool's composition check
    for t in range(5):
        assert corkscrew(res, "collections_pledged", t)[1] == 90000
    for t in range(5, 10):
        assert corkscrew(res, "collections_pledged", t)[1] == 18000
    assert corkscrew(res, "host_a_savings", 0)[1] == 72000          # the anchor is 80% of every receipt
    assert flow(res, "platform_pocket", 3) == 54000 - 45000
    assert flow(res, "platform_pocket", 6) == 10800 - 45000
    assert flow(res, "hosts_collective", 6) == 7200
    assert closing(res, "host_a_savings", 4) == 5 * 72000           # the anchor's sub-ledger flatlines
    assert closing(res, "host_a_savings", 9) == 5 * 72000
    members = sum(closing(res, f"host_{h}_savings", 9) for h in "abcdef")
    pooled = sum(corkscrew(res, "collections_pledged", t)[1] for t in range(10))
    assert members == pooled                                        # the panel's consolidation identity


# ── W4: policy stop ──────────────────────────────────────────────────────────

def test_w4_policy_stop():
    res = run("panel_base", "w4_policy_stop")
    recs = res.records("collections_route")
    assert [recs[t]["status"] for t in range(10)] == ["operative"] * 4 + ["suspended"] * 6
    for t in range(4, 10):
        assert flow(res, "collections_pledged", t) == 0
        assert flow(res, "senior_funder", t) == 0
        assert flow(res, "platform_pocket", t) == -12000
    assert closing(res, "fee_payers", 3) == -4 * 84000 == closing(res, "fee_payers", 9)
    assert closing(res, "platform_pocket", 3) == 36000
    assert closing(res, "platform_pocket", 9) == -36000


# ── W5: wrapper creditor priority, on ERIA ───────────────────────────────────

ERIA_ESTATE = 8 * F("0.8") * Q                                     # the ESCO's cash at realization
PANEL_ESTATE = F("1279292.0576")                                   # test_m6.FULL_ESTATE
PAR_RANKS = F(700000) + F(850000)


def owner_flow(res, t):
    """What the owner receives at realization (negative: pays in). equity_pocket is equity: a
    distribution lowers it, a call raises it, so the owner's flow is its movement turned round."""
    return -flow(res, "equity_pocket", t)


@pytest.fixture(scope="module")
def eria_w5():
    return run("eria", "w5_wrapper_priority")


def test_w5_wrapper_ranks_first_on_eria(eria_w5):
    res = eria_w5
    assert closing(res, "cash", 8) == 0 and closing(res, "estate_proceeds", 8) == 0      # swept, distributed
    assert res.records("realization")[8]["available"] == ERIA_ESTATE == F("2129292.0576")
    assert flow(res, "wrapper_creditors", 8) == 700000                                 # at par
    assert -flow(res, "share_capital", 8) == 850000 and closing(res, "share_capital", 8) == 0
    assert res.records("realization")[8]["call"] == 0
    assert owner_flow(res, 8) == ERIA_ESTATE - PAR_RANKS == F("579292.0576")
    ev = events(res, 8)
    assert ev.index("realization.wrapper_lien.paid:8") < ev.index("realization.capital_recovery.paid:8") \
        < ev.index("realization.residual:8")


@pytest.mark.xfail(strict=True, reason=(
    "one_engine's ERIA books the 850,000 the owner pays in (equity_funding), so the estate is the ESCO's cash "
    "2,129,292.0576 and the owner keeps +579,292.0576; the panel's ERIA never books that 850,000 yet recovers it "
    "from an estate net of it: estate 1,279,292.0576, a call of -270,707.9424"))
def test_w5_panel_number_the_owner_funds_a_call(eria_w5):
    assert eria_w5.records("realization")[8]["available"] == PANEL_ESTATE
    assert owner_flow(eria_w5, 8) == F("-270707.9424")


# ── W6: origination starvation ──────────────────────────────────────────────

def test_w6_origination_starvation():
    res = run("panel_pool_base", "w6_origination_starvation")
    recs = res.records("pool")
    assert recs[3]["entered"] == ["host_d", "host_e"]                    # squeaked in at 95
    assert recs[6]["skipped"] == {"host_f": ["origination-viability"]}
    assert closing(res, "host_f_savings", 9) == 0
    assert corkscrew(res, "collections_pledged", 1)[1] == 45000
    assert corkscrew(res, "collections_pledged", 7)[1] == 75000
    for t in range(3):
        assert closing(res, "platform_pocket", t) == -18000 * (t + 1)
    for t in range(3, 10):
        assert closing(res, "platform_pocket", t) == -54000


# ── W7: input-price shock ───────────────────────────────────────────────────

def test_w7_input_price_shock():
    res = run("panel_supply_base", "w7_input_price_shock")
    assert closing(res, "provider_trading", 0) == 10000
    assert closing(res, "provider_trading", 1) < 0                       # the first deficit period

    def cost(s):
        return F(100000) * (1 + F(min(s, 6), 3))
    for t in range(8):
        assert flow(res, "utility_supplier", t) == cost(t)
        assert closing(res, "provider_trading", t) == 110000 * (t + 1) - sum(cost(s) for s in range(t + 1))
    assert closing(res, "provider_trading", 7) == -820000
    plain = run_case(base("panel_supply_base"))
    for t in range(8):
        assert flow(res, "clients", t) == flow(plain, "clients", t)      # the client is untouched


# ── W8: compliance liability shock ──────────────────────────────────────────

def test_w8_compliance_shock():
    res = run("panel_base", "w8_compliance_shock")
    rec = res.records("originator_compliance")[5]
    assert rec["violated"] is True and rec["remedy_paid"] == 75000
    assert flow(res, "senior_funder", 5) == 63000                        # the waterfall is untouched
    assert flow(res, "platform_pocket", 5) == 21000 - 12000 - 75000
    assert closing(res, "platform_pocket", 4) == 45000
    assert closing(res, "platform_pocket", 5) == -21000
    assert flow(res, "compliance_enforcement", 5) == 75000


# ── W9: single-market regime ────────────────────────────────────────────────

def test_w9_single_market_regime():
    res = run("panel_pool_base", "w9_single_market_regime")
    regime = F("0.55")
    for t in range(4):
        assert flow(res, "platform_pocket", t) == 54000 - 45000
    for t in range(4, 10):
        assert flow(res, "platform_pocket", t) == 54000 * regime - 45000
        assert flow(res, "hosts_collective", t) == 36000
        assert corkscrew(res, "collections_pledged", t)[1] == 90000
        assert closing(res, "collections_pledged", t) == 54000 * (1 - regime) * (t - 3)
    assert closing(res, "collections_pledged", 9) == 145800


# ── the combined stress: W2 + W5 on ERIA ────────────────────────────────────

@pytest.fixture(scope="module")
def four_runs():
    return {"plain": run("eria"), "w2": run("eria", "w2_underperformance"),
            "w5": run("eria", "w5_wrapper_priority"), "both": run("eria", "w2_underperformance", "w5_wrapper_priority")}


def test_combined_stress_w2_plus_w5(four_runs):
    """Both worlds at once: the ramp shrinks the estate the par ranks feed on; the ranks still take par and
    the WHOLE revenue loss lands on the owner, one for one (the interaction line)."""
    runs = four_runs
    for i, r in enumerate(ERIA_RAMP):           # W2's ramp, read as the cash account's gross receipts
        assert corkscrew(runs["both"], "cash", i + 1)[1] == F("0.8") * r * Q
    for k in ("w5", "both"):                    # W5's par ranks, in every run
        assert flow(runs[k], "wrapper_creditors", 8) == 700000
        assert -flow(runs[k], "share_capital", 8) == 850000
    combined, w5_alone = owner_flow(runs["both"], 8), owner_flow(runs["w5"], 8)
    w2_position, plain_position = closing(runs["w2"], "cash", 8), closing(runs["plain"], "cash", 8)
    assert len({combined, w5_alone, w2_position, plain_position}) == 4
    revenue_loss = plain_position - w2_position
    assert revenue_loss == F("0.8") * Q * sum(1 - r for r in ERIA_RAMP) == F("558939.16512")
    assert combined - w5_alone == -revenue_loss                          # the interaction line, to the cent
    assert combined == F("20352.89248")
    print(f"\ncombined stress (one_engine's ERIA): revenue loss {float(revenue_loss):,.2f}; owner "
          f"{float(w5_alone):,.2f} (W5 alone) -> {float(combined):,.2f} (combined); par ranks 700,000 + 850,000")


@pytest.mark.xfail(strict=True, reason=(
    "inherits both ERIA differences: one_engine's revenue loss is 0.8 x q x sum(1 - perf) = 558,939.16512 "
    "(proportional split), not 332,701.884 (provider first); and the owner starts from +579,292.0576, not "
    "-270,707.9424 (the 850,000 paid in is on the books), so the combined position is +20,352.89, not -603,409.83"))
def test_combined_stress_panel_numbers(four_runs):
    runs = four_runs
    assert closing(runs["plain"], "cash", 8) - closing(runs["w2"], "cash", 8) == F("332701.884")
    assert round(float(owner_flow(runs["both"], 8)), 2) == -603409.83


# ── the panel is data ────────────────────────────────────────────────────────

def test_the_worlds_are_pure_data():
    files = sorted(WORLDS.glob("*.yaml"))
    assert len(files) == 10
    for f in files:
        doc = read(f)
        assert set(doc) <= WORLD_KEYS, (f.name, sorted(set(doc) - WORLD_KEYS))
        for rb in ((doc.get("add") or {}).get("rulebooks") or {}).values():
            assert rb["kind"] in RULEBOOKS, (f.name, rb["kind"])


def test_the_engine_names_no_case():
    """The worlds are data: no engine module names the panel's cases or their vocabulary."""
    engine = "\n".join(p.read_text() for p in (CASES.parent).rglob("*.py")
                       if "tests" not in p.parts and "oracles" not in p.parts).lower()
    # ERIA is left out: packs/contracts.py was built for the ERIA case and has
    # always named it in its docstring (provenance, not branching on the case).
    for word in ("metronet", "tfl", "andhra", "pradesh", "enernoc", "pjm", "carillion", "green deal"):
        assert not re.search(rf"\b{re.escape(word)}\b", engine), word


def test_the_panel_is_deterministic():
    one, two = run("metronet", "w1_servicer_insolvency"), run("metronet", "w1_servicer_insolvency")
    assert [(p.event, p.amount) for p in book(one).postings] == [(p.event, p.amount) for p in book(two).postings]
    assert one.group.records == two.group.records


def test_the_composer_refuses_what_the_base_does_not_have():
    with pytest.raises(ValueError, match="has no"):
        compose(base("panel_base"), {"world": "stray", "set": {"rulebooks.pool.thresholds": []}})
    with pytest.raises(ValueError, match="already in the base"):
        compose(base("panel_base"), {"world": "twice", "add": {"opening.deal": {"fee_payers": {}}}})
    with pytest.raises(ValueError, match="binds nothing"):
        compose(base("panel_supply_base"), world("w2_underperformance"))
    with pytest.raises(ValueError, match="unknown keys"):
        compose(base("panel_base"), {"world": "typo", "sett": {}})
