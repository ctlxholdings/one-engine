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
