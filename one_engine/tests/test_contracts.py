"""Tests for one_engine.contracts (card 6) and one_engine.entities (card 5)."""
import pytest

from one_engine.contracts import Contract, ContractError
from one_engine.entities import Group
from one_engine.inputs import State, Value
from one_engine.packs.contracts import SHARED_SAVINGS


def stated(v):
    return Value(v, State.STATED, "test")


TERMS = {"kwh": stated(1000), "tariff": stated("0.1"), "performance": stated(1),
         "split": stated("0.8"), "start": stated(1), "terms": stated(2)}


def contract(**over):
    terms = dict(TERMS, **over)
    return Contract("ss1", SHARED_SAVINGS, parties={"provider": "esco", "client": "host"}, terms=terms,
                    currency="USD")


def test_an_unknown_term_refuses():
    with pytest.raises(ContractError, match="tarif"):
        contract(tarif=stated("0.1"))


def test_a_missing_term_refuses():
    terms = dict(TERMS)
    del terms["split"]
    with pytest.raises(ContractError, match="split"):
        Contract("ss1", SHARED_SAVINGS, parties={"provider": "esco", "client": "host"}, terms=terms,
                 currency="USD")


def run(c, periods=3):
    g = Group(periods=periods, phases=("accrue", "settle"))
    g.add_contract(c)
    g.run()
    return g


def test_both_sides_of_an_intercompany_fee_are_booked():
    g = run(contract())
    assert g.book("esco").balance("cash", 2) == 160          # 2 x 0.8 x 1000 x 0.1
    assert g.book("host").balance("cash", 2) == 40           # avoided cost 200 less fees 160
    assert g.unpaired_flows() == []


def test_a_stand_in_tariff_reaches_the_provider_cash_as_stand_in():
    g = run(contract(tariff=Value("0.1", State.STAND_IN, "guess")))
    assert State.STAND_IN in g.book("esco").states("cash", 1)


def test_performance_may_be_a_series_one_value_per_year_of_the_term():
    # added for the failure panel's W2 on ERIA: the measured savings slide down a declared ramp,
    # and the provider's fee is its share of what was measured (the split is proportional)
    g = run(contract(performance=stated(["1", "0.5"])))
    esco, host = g.book("esco"), g.book("host")
    assert [esco.balance("cash", t) - esco.opening("cash", t) for t in (1, 2)] == [80, 40]
    assert [host.balance("avoided_energy_cost", t) - host.opening("avoided_energy_cost", t) for t in (1, 2)] == [100, 50]


# ── added for the nightmare case: accounts in a second unit, given figures ──

from fractions import Fraction as F  # noqa: E402

from one_engine.contracts import ContractType  # noqa: E402
from one_engine.kernel import Nature  # noqa: E402
from one_engine.packs.contracts import FOREIGN_LOAN, LOAN  # noqa: E402


def _facility(ctype, **over):
    terms = dict(commitment=100, draw_schedule=[100, 0, 0, 0], available_until=0, rate="0.1", rate_from=1,
                 days_per_period=360, day_count_basis=360, commitment_fee_rate=0, capitalise_from=0,
                 capitalise_until=-1, repay_from=2, repay_periods=2, sculpt_dscr=None, principal_schedule=None,
                 arrangement_fee=0, fee_at=0)
    if ctype is FOREIGN_LOAN:
        terms.update(currency="USD", fx_rate=["0.9", "0.9", "1.0", "1.0"])
    terms.update(over)
    return Contract("b", ctype, {"borrower": "spv"}, {k: stated(v) for k, v in terms.items()}, "EUR")


def test_an_account_takes_its_unit_from_a_term():
    accts = dict((n, u) for n, _, u in _facility(FOREIGN_LOAN).accounts()["spv"])
    assert accts["b.debt"] == "EUR" and accts["b.debt_fc"] == "USD" and accts["b.fc_position"] == "USD"
    with pytest.raises(ContractError, match="unknown term 'ccy'"):
        ContractType("x", ("a",), ("p",), {"p": (("cash", Nature.ASSET, "ccy"),)}, (), lambda *a: {})


def test_a_claim_that_needs_a_figure_nobody_passed_refuses():
    from one_engine.contracts import Balances
    g = Group(periods=4, phases=["fund", "accrue", "pay", "close"])
    loan = _facility(LOAN, sculpt_dscr="1.5")
    g.add_contract(loan)
    g.register("fund", lambda g, t: g.settle(g.claims(t)["b.draw"], 100, "fund") if t == 0 else None)
    g.run()
    with pytest.raises(ContractError, match="'cfads'"):
        loan.claims(2, Balances(loan, g.books, 2))
    due = {c.spec.name: c.due for c in loan.claims(2, Balances(loan, g.books, 2, {"cfads": 60}))}
    assert due["interest"] == 20 and due["principal"] == 60 / F(3, 2) - 20     # CFADS / target less interest


def test_a_list_of_references_resolves_to_contracts():
    from one_engine.packs.contracts import DEBT_RESERVE
    a, b = _facility(LOAN), _facility(LOAN)
    b.id = "c"
    r = Contract("r", DEBT_RESERVE, {"holder": "spv"},
                 {"covers": stated(["b", "c"]), "periods_forward": stated(1), "from": stated(0),
                  "prefund": stated([])}, "EUR")
    r.resolve({"b": a, "c": b})
    assert r.terms["covers"].value == [a, b]
    with pytest.raises(ContractError, match="unknown contract 'z'"):
        Contract("r", DEBT_RESERVE, {"holder": "spv"},
                 {"covers": stated(["b", "z"]), "periods_forward": stated(1), "from": stated(0),
                  "prefund": stated([])}, "EUR").resolve({"b": a})


# ── added for Bodmer's solar case: a principal schedule, series terms ──

def test_a_negative_scheduled_repayment_capitalises_that_much_interest():
    g = Group(periods=4, phases=["fund", "accrue", "pay", "close"])
    loan = _facility(LOAN, rate_from=0, repay_from=1, repay_periods=3, principal_schedule=[0, "-4", 50, 0])
    g.add_contract(loan)
    g.register("fund", lambda g, t: g.settle(g.claims(t)["b.draw"], 100, "fund") if t == 0 else None)
    g.run()
    b = g.book("spv")
    # period 1: interest 10 on 100, of which 4 is capitalised and 6 stays due
    assert b.balance("b.capitalised_interest", 1) == 4 and b.balance("b.interest_payable", 1) == 6


def test_a_scheduled_repayment_below_minus_the_interest_refuses():
    g = Group(periods=4, phases=["fund", "accrue", "pay", "close"])
    loan = _facility(LOAN, rate_from=0, repay_from=1, repay_periods=3, principal_schedule=[0, -11, 0, 0])
    g.add_contract(loan)
    g.register("fund", lambda g, t: g.settle(g.claims(t)["b.draw"], 100, "fund") if t == 0 else None)
    with pytest.raises(ValueError, match="new draw"):
        g.run()


def test_a_loan_cannot_be_both_sculpted_and_scheduled():
    loan = _facility(LOAN, sculpt_dscr="1.5", principal_schedule=[0, 0, 50, 50])
    g = Group(periods=4, phases=["fund", "accrue", "pay", "close"])
    g.add_contract(loan)
    with pytest.raises(ValueError, match="not both"):
        g.run()


def test_days_per_period_may_be_a_series():
    g = Group(periods=3, phases=["fund", "accrue", "pay", "close"])
    loan = _facility(LOAN, rate_from=0, days_per_period=[30, 90, 90], draw_schedule=[100, 0, 0])
    g.add_contract(loan)
    g.register("fund", lambda g, t: g.settle(g.claims(t)["b.draw"], 100, "fund") if t == 0 else None)
    g.run()
    # a quarter of 10% a year on 100
    assert g.book("spv").balance("b.interest", 1) - g.book("spv").opening("b.interest", 1) == F(10) / 4


def test_a_performance_series_must_cover_the_term_exactly():
    with pytest.raises(ValueError, match="term of 2"):
        run(contract(performance=stated(["1", "0.5", "0.5"])))
