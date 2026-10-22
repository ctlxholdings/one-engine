"""M4 — the transform and metric layers: OP4/OP9 functional; the two
failure-world reproductions through the engine as pure configs.

The expected values below are the prior independent blind runs' computed
figures for the two briefs (blind_briefs/orc-c05.md and orc-c31v2.md,
each worked blind from the brief alone — blind-build-c05/,
blind-build-c31v2/):

  Bulb (orc-c05): accumulated trading deficit GBP -2.29bn aggregate
  (-1,526.60/customer), asserted here against the published oracle BAND
  2.0-2.58bn with the point value reported; mechanism: the fixed-price
  commitment meets an unhedged wholesale shock (the wholesale path is an
  OP4 transform input), the deficit accrues on the supplier through the Nov
  2021 special administration, the Dec 2022 sale transfers the book not the
  loss, the state financed it, and a levy socializes it across ALL energy
  bills. First deficit month Feb 2021; worst month Jan 2022 (heating
  season).

  Indiana Toll Road v2 (orc-c31v2): interest-only bullet 3,030 with the
  PRESET accreting rate path 3% -> 11.3% by 2023 carried as an OP4 transform
  on rates (the period charge applied to the debt balance); actual vs
  underwritten traffic as volume paths; retained-cash buffer; FIRST MISSED
  PAYMENT June 2014 (t=15) ~102.5 within +-15%; debt at default (CASH
  basis: 3,030 + the capitalized miss) ~3,132; 2015 sale at the
  underwriting multiple marked to actual volume; distribution to creditors
  ~88.9c +-5c of face; equity wiped; the state's +3,800 never reverses.
  Basis note (R-orc-8 recorded): the CASH-basis figure is asserted;
  claim-basis divergence is NOT expected.

If an expected value fails, the ENGINE (or a fixture config) is wrong —
never the expected value. The fixtures are YAML data files; every path and
the calibrated revenue base are data with their derivations documented in
the fixture comments.
"""
from fractions import Fraction
from pathlib import Path

import pytest
import yaml

from oe.engine.deal import DealConfigError, load_deal
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


def _green(trace):
    for label, ids in trace:
        assert ids["duality"] and ids["sum_zero"] and ids["assertions"], (label, ids)


def _tmp(tmp_path, doc, name="deal.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(yaml.safe_load(doc) if isinstance(doc, str) else doc))
    return p


# =============================================================================
# Bulb — the fixed-price commitment against the unhedged input-price shock
# =============================================================================

@pytest.fixture(scope="module")
def bulb(specs):
    return _run("bulb", specs)


def test_bulb_identities_green_every_period(bulb):
    _, trace = bulb
    assert len(trace) == 24                    # Jan 2021 - Dec 2022, monthly
    _green(trace)


def test_bulb_accumulated_deficit_in_the_published_band(bulb):
    """The oracle: accumulated deficit in the GBP 2.0-2.58bn band (prior
    independent run: -2.29bn; the levy posting IS the deficit — the state's
    fund receives exactly what it financed)."""
    session, _ = bulb
    levy = next(p for p in session.ledger.postings(23)
                if any(l.account == "energy_bill_payers" for l in p.legs))
    deficit = float(-next(l.amount for l in levy.legs if l.account == "energy_bill_payers"))
    print(f"\nBulb accumulated deficit: GBP {deficit:,.0f} "
          f"({deficit / 1e9:.4f}bn; prior independent run -2.29bn, band 2.0-2.58bn)")
    assert 2.0e9 <= deficit <= 2.58e9


def test_bulb_the_shock_path_and_seasonality(bulb):
    """Mechanism: breakeven calibration means Jan 2021 margins to zero (the
    first month at anchor prices); the first deficit lands Feb 2021 as the
    ramp begins; the worst month is deep winter (Jan 2022) at the held peak
    — heating-season consumption weights amplify the held-peak months."""
    session, _ = bulb
    flows = [session.ledger.flow("supplier_trading", t) for t in range(24)]
    assert abs(float(flows[0])) < 0.05e6            # anchor month: margin ~ 0
    assert flows[1] < 0                              # first deficit Feb 2021
    worst = flows.index(min(flows))
    assert worst == 12                               # Jan 2022 (t=0 = Jan 2021)
    assert float(flows[12]) == pytest.approx(-225.46e6, abs=0.2e6)


def test_bulb_mechanism_state_finances_sale_transfers_book_levy_socializes(bulb):
    """The published mechanism, as ledger assertions: the supplier's deficit
    is cleared by the state's administration fund at the sale (the book
    transfers, the loss does not); the fund is made whole by a levy on ALL
    energy bills (socialized); customers paid the fixed tariff throughout
    (no pass-through), the wholesale market received the unhedged spot cost."""
    session, _ = bulb
    ledger = session.ledger
    handoff = next(p for p in ledger.postings(23)
                   if any(l.account == "state_admin_fund" and l.amount < 0 for l in p.legs))
    levy = next(p for p in ledger.postings(23)
                if any(l.account == "energy_bill_payers" for l in p.legs))
    assert handoff.legs[1].account == "supplier_trading"          # state finances
    assert handoff.legs[0].amount + levy.legs[1].amount == 0      # levy == deficit
    assert ledger.closing_balance("supplier_trading", 23) == 0    # book transferred, loss left
    assert ledger.closing_balance("state_admin_fund", 23) == 0    # levy recovers it fully
    assert ledger.closing_balance("energy_bill_payers", 23) == levy.legs[0].amount
    # the fixed tariff was paid throughout: ~2 years x 1,275 x 1.5m customers
    # (the small excess over 3.825bn is the 6dp weight rounding in the data)
    assert float(-ledger.closing_balance("customers_billbase", 23)) == \
        pytest.approx(3.825e9, abs=1e4)
    # the unhedged shock landed on the wholesale market (gross spot cost)
    assert float(ledger.closing_balance("wholesale_market", 23)) == \
        pytest.approx(3.1498e9, abs=1e5)


def test_bulb_positions_and_horizon_metrics_recorded(bulb):
    session, _ = bulb
    pos = session.metrics["position"]
    assert set(pos) == {"supplier_trading", "customers_billbase", "wholesale_market",
                        "network_policy", "energy_bill_payers", "state_admin_fund"}
    assert all(len(series) == 24 for series in pos.values())       # exact Fractions
    assert sum(pos["supplier_trading"], Fraction(0)) == 0          # one pocket, cleared
    assert session.metrics["horizon"]["totals"]["state_admin_fund"] == 0


# =============================================================================
# Indiana Toll Road v2 — the accreting bullet under actual traffic
# =============================================================================

@pytest.fixture(scope="module")
def itr(specs):
    return _run("indiana-toll-road", specs)


def test_itr_identities_green_every_period(itr):
    _, trace = itr
    assert len(trace) == 18                    # H2-2006 .. H1-2015, half-years
    _green(trace)


def test_itr_first_missed_payment_june_2014(itr):
    """t=15 ends June 2014. Through t=14 (Dec 2013) every claim was paid in
    full (senior_debt still at the drawn 3,030); at t=15 the cash cannot
    cover the claim — nothing is paid (binary miss) and the full claim
    capitalizes: 102.4655 against the ~102.5 anchor, well inside +-15%."""
    session, _ = itr
    ledger = session.ledger
    assert ledger.closing_balance("senior_debt", 14) == 3030
    assert ledger.flow("senior_lenders", 15) == 0                    # nothing paid
    missed = ledger.closing_balance("senior_debt", 15) - 3030
    assert float(missed) == pytest.approx(102.46551, abs=1e-4)
    assert float(missed) == pytest.approx(102.5, abs=0.155 * 102.5)
    print(f"\nITR first missed payment: June 2014, USD {float(missed):.4f}m "
          f"(anchor ~102.5, deviation {abs(float(missed) - 102.5) / 102.5:.2%})")


def test_itr_debt_at_default_cash_basis(itr):
    """The cash-basis figure: 3,030 principal + the capitalized miss
    (R-orc-8: no claim-basis divergence expected or asserted)."""
    session, _ = itr
    debt = session.ledger.closing_balance("senior_debt", 15)
    assert debt == 3030 + Fraction(10246551, 100000)
    assert float(debt) == pytest.approx(3132.46551, abs=1e-4)
    assert abs(float(debt) - 3132.0) <= 0.15 * 3132.0


def test_itr_restructuring_capitalizes_at_the_accreting_schedule(itr):
    """Post-default (R-orc-8's accrual basis): the Dec-2014 and Jun-2015
    claims capitalize at the scheduled charge on the GROWING balance — the
    preset accreting path keeps compounding through the restructuring."""
    session, _ = itr
    ledger = session.ledger
    d15 = ledger.closing_balance("senior_debt", 15)
    d16 = ledger.closing_balance("senior_debt", 16)
    assert float(d16 - d15) == pytest.approx(float(d15) * 0.035038, abs=1e-3)
    assert ledger.flow("senior_lenders", 16) == 0            # still nothing paid


def test_itr_sale_at_the_underwriting_multiple_marked_to_actual_volume(itr):
    """Price = 3,800 x actual/underwritten volume at sale (0.893 / 1.22):
    the original price marked to actual performance — the +22% forecast was
    in the 2006 price."""
    session, _ = itr
    sale = next(p for p in session.ledger.postings(17)
                if any(l.account == "buyer_2015" for l in p.legs))
    price = -next(l.amount for l in sale.legs if l.account == "buyer_2015")
    assert float(price) == pytest.approx(3800.0 * 0.893 / 1.22, abs=0.01)


def test_itr_distribution_recovery_and_shortfall(itr):
    """Estate = retained buffer + sale price, distributed in full to the
    secured creditors against face: ~88.9c on the dollar (band +-5c);
    equity residual exactly zero (wiped); the shortfall stays on the debt
    account as the lenders' unrealized claim."""
    session, _ = itr
    ledger = session.ledger
    dist = ledger.flow("senior_lenders", 17)
    face = dist + ledger.closing_balance("senior_debt", 17) \
        + ledger.closing_balance("interest_claim", 17)
    recovery = float(dist / face)
    print(f"\nITR sale: distribution USD {float(dist):,.2f}m against face USD "
          f"{float(face):,.2f}m = {recovery * 100:.2f}c "
          f"(prior independent run 88.9c, band 83.9-93.9c)")
    assert 0.839 <= recovery <= 0.939
    assert float(face) == pytest.approx(3359.78, abs=0.05)
    assert ledger.closing_balance("spv_cash", 17) == 0          # equity wiped
    assert float(ledger.closing_balance("senior_debt", 17)) == \
        pytest.approx(372.63, abs=0.05)                          # the shortfall


def test_itr_the_states_upfront_payment_never_reverses(itr):
    """Non-recourse: the state's +3,800 at close is never touched again —
    a ledger assertion at every period, not a narrative one."""
    session, _ = itr
    ledger = session.ledger
    for t in range(18):
        assert ledger.closing_balance("state_cash", t) == 3800, t
    assert ledger.flow("state_cash", 0) == 3800


def test_itr_retained_cash_buffer_discipline(itr):
    """The buffer the coverage shortfall drains: operating cash never goes
    negative (opex rank 1 served first, misses preserve the estate)."""
    session, _ = itr
    for t in range(18):
        assert session.ledger.closing_balance("spv_cash", t) >= 0, t


def test_itr_positions_and_horizon_metrics_recorded(itr):
    session, _ = itr
    pos = session.metrics["position"]
    assert set(pos) == {"senior_lenders", "spv_cash", "state_cash", "senior_debt"}
    closing = session.metrics["horizon"]["closing"]
    assert closing["state_cash"] == 3800
    assert closing["spv_cash"] == 0
    # lenders over the whole deal, cash basis: -3,030 lent + 1,093.20 interest
    # received + the 2,987.15 distribution = +1,050.35 net cash — profitable in
    # cash against principal, 372.63 SHORT of the accrued face (the shortfall
    # sits on senior_debt, asserted above)
    assert float(closing["senior_lenders"]) == pytest.approx(1050.35, abs=0.05)


# =============================================================================
# determinism (both fixtures re-run identically)
# =============================================================================

@pytest.mark.parametrize("name", ["bulb", "indiana-toll-road"])
def test_failure_world_reproductions_are_deterministic(specs, name):
    a = Session(load_deal(FIXTURES / f"{name}.yaml", specs), specs)
    a.run()
    b = Session(load_deal(FIXTURES / f"{name}.yaml", specs), specs)
    b.run()
    assert [(p.label, p.legs) for p in a.ledger.postings()] == \
        [(p.label, p.legs) for p in b.ledger.postings()]
    assert a.metrics == b.metrics


# =============================================================================
# unit level: OP4's dials, the interest_only bullet, OP9's metrics
# =============================================================================

TRANSFORM_DEAL = """
deal: unit-transform
horizon: 5
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [resource, payee_cash, stock_acct, reader]}
parties:
  - {name: payer-party, role: payer}
  - {name: payee-party, role: payee}
instruments:
  - operator: op4_transform
    index_ref: {escalation: {ramp: {from: 1.0, to: 2.0, periods: 4}}}
    coefficient: 100
    lag: 1
    unit_conversion: {per_unit_to_ledger: 2.0}
    payer: resource
    payee: payee_cash
    schedule_ref: {start: 0, terms: 5, phase: accrue}
"""


def test_op4_ramp_lag_coefficient_and_unit_conversion(tmp_path, specs):
    """ramp 1->2 over 4 (held at 2), read one period behind (lag 1, clamped
    at the first value), scaled by the coefficient and the unit conversion."""
    session = Session(load_deal(_tmp(tmp_path, TRANSFORM_DEAL), specs), specs)
    _green(session.run())
    got = [float(session.ledger.flow("payee_cash", t)) for t in range(5)]
    assert got == [200.0, 200.0, 250.0, 300.0, 350.0]


DEGRADE_DEAL = """
deal: unit-degrade
horizon: 3
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [resource, payee_cash]}
parties:
  - {name: payer-party, role: payer}
instruments:
  - operator: op4_transform
    index_ref: 10.0
    degradation_rate: 0.5
    payer: resource
    payee: payee_cash
    schedule_ref: {start: 0, terms: 3, phase: accrue}
"""


def test_op4_degradation_decays_geometrically(tmp_path, specs):
    session = Session(load_deal(_tmp(tmp_path, DEGRADE_DEAL), specs), specs)
    _green(session.run())
    got = [float(session.ledger.flow("payee_cash", t)) for t in range(3)]
    assert got == [10.0, 5.0, 2.5]


APPLY_DEAL = """
deal: unit-apply
horizon: 3
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [resource, stock_acct, flow_payee, balance_payee]}
parties:
  - {name: payer-party, role: payer}
instruments:
  - {operator: op1_instrument_generation, instrument_type: fixed_fee, terms: 3,
     notional: 100.0, payer: resource, payee: stock_acct,
     schedule_ref: {start: 0, phase: accrue}}
  - operator: op4_transform
    index_ref: 0.5
    apply_to: {account: stock_acct, read: flow}
    payer: resource
    payee: flow_payee
    schedule_ref: {start: 1, terms: 2, phase: settle}
  - operator: op4_transform
    index_ref: 0.1
    apply_to: {account: stock_acct, read: closing_balance}
    payer: resource
    payee: balance_payee
    schedule_ref: {start: 2, terms: 1, phase: settle}
"""


def test_op4_apply_to_reads_the_ledger_at_fire_time(tmp_path, specs):
    """A pure read, never a debit: flow reads the account's period flow at
    fire time (settle phase: 100 accrued in accrue), closing_balance its
    corkscrew position."""
    session = Session(load_deal(_tmp(tmp_path, APPLY_DEAL), specs), specs)
    _green(session.run())
    ledger = session.ledger
    assert float(ledger.flow("flow_payee", 1)) == 50.0     # 0.5 x the t=1 flow
    assert float(ledger.flow("flow_payee", 2)) == 50.0
    assert float(ledger.flow("balance_payee", 2)) == 30.0  # 0.1 x closing 300


def test_op4_bare_declaration_and_nonpositive_transforms_post_nothing(tmp_path, specs):
    doc = yaml.safe_load(TRANSFORM_DEAL)
    for key in ("payer", "payee"):                          # a bare declaration
        del doc["instruments"][0][key]
    doc2 = yaml.safe_load(yaml.safe_dump(doc))
    doc["instruments"][0]["index_ref"] = 0.0                # nothing transformable
    for d in (doc, doc2):
        session = Session(load_deal(_tmp(tmp_path, d), specs), specs)
        assert len(session.ledger.postings()) == 0
        _green(session.run())


def test_op4_refuses_malformed_configs(tmp_path, specs):
    base = yaml.safe_load(TRANSFORM_DEAL)
    cases = [
        {"index_ref": {"esc": {"ramp": {"from": 1.0}}}},             # ramp missing to/periods
        {"index_ref": {"esc": [1.0, 2.0]}},                          # wrong path length
        {"lag": -1},
        {"degradation_rate": 1.5},
        {"unit_conversion": [0.5, 0.5]},                              # conversions are constants
        {"apply_to": {"account": "stock_acct", "read": "mid_balance"}},
        {"apply_to": {"account": "undeclared", "read": "flow"}},
        {"payee": None},                                               # one counterparty only
    ]
    for patch in cases:
        doc = yaml.safe_load(yaml.safe_dump(base))
        doc["instruments"][0].update(patch)
        with pytest.raises(DealConfigError):
            Session(load_deal(_tmp(tmp_path, doc), specs), specs)


BULLET_DEAL = """
deal: unit-bullet
horizon: 3
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [funding, debt, claim, expense, spv, lender, world]}
parties:
  - {name: borrower, role: payer}
  - {name: lender-party, role: lender}
instruments:
  - operator: op1_instrument_generation
    instrument_type: interest_only
    terms: 3
    notional: 100.0
    payer: spv
    payee: lender
    debt_account: debt
    interest_claim: claim
    interest_expense: expense
    funding_source: funding
    schedule_ref: {start: 0, phase: accrue}
  - operator: op4_transform
    index_ref: {income: INCOME}
    payer: world
    payee: spv
    schedule_ref: {start: 0, terms: 3, phase: accrue}
  - operator: op4_transform
    index_ref: 0.1
    apply_to: {account: debt, read: closing_balance}
    payer: expense
    payee: claim
    schedule_ref: {start: 0, terms: 3, phase: accrue}
"""


@pytest.mark.parametrize("income,lender_flows,debt_closings,spv_final", [
    ([60, 60, 60], [10, 10, 110], [100, 100, 0], 50.0),   # served; bullet repaid in full
    ([6, 6, 6], [0, 0, 18], [110, 121, 115.1], 0.0),      # default at t0, compounding
])
def test_op1_interest_only_pay_capitalize_bullet(tmp_path, specs, income, lender_flows,
                                                 debt_closings, spv_final):
    """The serviced bullet: claims paid while cash covers them (with their
    discharge), a miss capitalizes and every later claim accrues on the
    growing balance, and maturity sweeps min(cash, balance) (claim discharged
    first, then principal; excess cash stays with the payer)."""
    doc = BULLET_DEAL.replace("INCOME",
                              yaml.safe_dump(income, default_flow_style=True).strip())
    session = Session(load_deal(_tmp(tmp_path, doc), specs), specs)
    _green(session.run())
    ledger = session.ledger
    assert [float(ledger.flow("lender", t)) for t in range(3)] == lender_flows
    assert [float(ledger.closing_balance("debt", t)) for t in range(3)] == debt_closings
    assert float(ledger.closing_balance("spv", 2)) == spv_final
    for t in range(3):
        assert ledger.closing_balance("claim", t) == 0     # claim settles every period


METRICS_DEAL = """
deal: unit-metrics
horizon: 4
settings:
  op7_clock: {period_granularity: year, phases: [open, accrue, settle, close]}
  op8_ledger: {accounts: [investor, investee]}
parties:
  - {name: investor-party, role: investor}
instruments:
  - {operator: op1_instrument_generation, instrument_type: fixed_fee, terms: 1,
     notional: 250.0, payer: investor, payee: investee,
     schedule_ref: {start: 0, phase: accrue}}
  - {operator: op1_instrument_generation, instrument_type: fixed_fee, terms: 3,
     notional: 100.0, payer: investee, payee: investor,
     schedule_ref: {start: 1, phase: accrue}}
  - operator: op9_metric_reduction
    metrics: [npv, irr, payback, position, horizon]
    discount_basis: {rate: 0.1}
    horizon: 4
    position_scope: [investor]
"""


def test_op9_npv_irr_payback_position_horizon(tmp_path, specs):
    """The investor's position [-250, +100, +100, +100]: NPV at the declared
    basis, IRR (annuity factor 2.5), simple payback 2.5, exact positions."""
    session = Session(load_deal(_tmp(tmp_path, METRICS_DEAL), specs), specs)
    _green(session.run())
    m = session.metrics
    assert len(session.ledger.postings()) == 4            # a pure reduction posts nothing
    annuity = sum(100.0 / 1.1 ** t for t in (1, 2, 3))
    assert m["npv"]["investor"] == pytest.approx(-250.0 + annuity, abs=1e-9)
    assert m["npv"]["investor"] == pytest.approx(-1.3148009016, abs=1e-8)
    # IRR: 100 x annuity(3, r) = 250 -> factor 2.5 -> r ~ 9.7010%
    assert m["irr"]["investor"] == pytest.approx(0.0970103, abs=1e-6)
    assert m["payback"]["investor"] == pytest.approx(2.5, abs=1e-12)
    assert m["position"]["investor"] == [Fraction(-250), Fraction(100),
                                         Fraction(100), Fraction(100)]
    assert m["horizon"]["totals"]["investor"] == Fraction(50)
    assert m["horizon"]["closing"]["investor"] == Fraction(50)


def test_op9_discount_basis_anchors_the_valuation(tmp_path, specs):
    """The C1 disposition made operational: t0 anchors the NPV — moving the
    anchor forward one period multiplies the value by (1 + rate)."""
    doc = yaml.safe_load(METRICS_DEAL)
    doc["instruments"][2]["discount_basis"] = {"rate": 0.1, "t0": 1}
    session = Session(load_deal(_tmp(tmp_path, doc), specs), specs)
    _green(session.run())
    base = yaml.safe_load(METRICS_DEAL)
    plain = Session(load_deal(_tmp(tmp_path, base, "plain.yaml"), specs), specs)
    plain.run()
    assert session.metrics["npv"]["investor"] == \
        pytest.approx(1.1 * plain.metrics["npv"]["investor"], abs=1e-6)


def test_op9_irr_none_without_a_bracket_and_payback_none_without_a_crossing(tmp_path, specs):
    doc = yaml.safe_load(METRICS_DEAL)
    del doc["instruments"][0]                     # no investment: flows never negative
    session = Session(load_deal(_tmp(tmp_path, doc), specs), specs)
    _green(session.run())
    assert session.metrics["irr"]["investor"] is None
    assert session.metrics["payback"]["investor"] is None


def test_op9_refuses_malformed_configs(tmp_path, specs):
    base = yaml.safe_load(METRICS_DEAL)
    cases = [
        {"metrics": ["roic"]},                                   # unknown metric
        {"metrics": []},                                         # empty
        {"horizon": 5},                                          # beyond the clock
        {"metrics": ["npv"], "discount_basis": None},            # npv needs a basis
        {"metrics": ["irr"], "position_scope": []},              # flow metric needs scope
        {"position_scope": ["undeclared"]},
        {"metrics": ["irr"], "position_scope": ["investor"],
         "discount_basis": {"rate": -2.0}},                      # rate <= -1
    ]
    for patch in cases:
        doc = yaml.safe_load(yaml.safe_dump(base))
        op9 = next(i for i in doc["instruments"] if i["operator"] == "op9_metric_reduction")
        op9.update(patch)
        with pytest.raises(DealConfigError):
            Session(load_deal(_tmp(tmp_path, doc), specs), specs)


# =============================================================================
# the birth-certificate statement, extended to M4
# =============================================================================

def test_the_engine_names_no_case_m4():
    """Config-only reproduction, M4 evidence: the engine package and the
    operator spec contain no failure-world name, party name or case
    vocabulary of the two M4 oracles (as words) — the fixtures are YAML
    data files, every path and calibrated parameter is data, and no engine
    behavior is keyed to a case."""
    import re

    def words(text, *needles):
        return [n for n in needles if re.search(rf"\b{re.escape(n)}\b", text)]

    engine = "\n".join(p.read_text() for p in (KIT / "oe" / "engine").glob("*.py")).lower()
    assert not words(engine, "bulb", "indiana", "toll", "concession", "c05", "c31",
                     "macquarie", "cintra", "fhwa", "levy", "wholesale")
    spec_text = (KIT / "operators.yaml").read_text().lower()
    assert not words(spec_text, "bulb", "indiana", "toll", "concession", "c05", "c31",
                     "macquarie", "cintra", "fhwa", "levy")
