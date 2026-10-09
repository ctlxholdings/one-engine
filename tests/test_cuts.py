"""Tests for one_engine.cuts (card 13): named totals by classification, never by hand-picked lists."""
import pytest

from one_engine.cuts import CutError, Scheme, cash_flow_cut
from one_engine.kernel import Account, Book, Nature, Posting


def book():
    b = Book(entity="esco", periods=2, phases=("settle",),
             accounts=[Account("cash", Nature.ASSET, "USD"), Account("equity", Nature.EQUITY, "USD"),
                       Account("asset", Nature.ASSET, "USD"), Account("revenue", Nature.INCOME, "USD")])
    b.post(Posting(period=0, phase="settle", debit="cash", credit="equity", amount=100,
                   effect={"cash": 1, "equity": 1}, source="t", event="fund"))
    b.post(Posting(period=0, phase="settle", debit="asset", credit="cash", amount=100,
                   effect={"asset": 1, "cash": -1}, source="t", event="capex"))
    b.close_period(0)
    b.post(Posting(period=1, phase="settle", debit="cash", credit="revenue", amount=130,
                   effect={"cash": 1, "revenue": 1}, source="t", event="fee"))
    b.close_period(1)
    return b


SCHEME = Scheme("cash_flow", {"equity": "financing", "asset": "investing", "revenue": "operating"})


def test_project_cash_flow_is_operating_plus_investing():
    series = cash_flow_cut(book(), "cash", SCHEME, include={"operating", "investing"})
    assert series.values == [-100, 130]


def test_an_unclassified_counter_account_refuses():
    partial = Scheme("cash_flow", {"equity": "financing", "asset": "investing"})
    with pytest.raises(CutError, match="revenue"):
        cash_flow_cut(book(), "cash", partial, include={"operating", "investing"})


def test_a_class_the_scheme_never_uses_refuses():
    with pytest.raises(CutError, match="operatng"):
        cash_flow_cut(book(), "cash", SCHEME, include={"operatng"})
