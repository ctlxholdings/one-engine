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


# ── P&L and balance cuts (Macabacus LBO, step 7f) ─────────────────────────

from one_engine.cuts import balance_cut, pnl_cut  # noqa: E402


def pl_book():
    b = Book(entity="co", periods=2, phases=("run",),
             accounts=[Account("cash", Nature.ASSET, "USD"), Account("debt", Nature.LIABILITY, "USD"),
                       Account("equity", Nature.EQUITY, "USD"), Account("sales", Nature.INCOME, "USD"),
                       Account("cogs", Nature.EXPENSE, "USD"), Account("interest", Nature.EXPENSE, "USD")],
             opening={"cash": 50, "debt": 30, "equity": 20})
    for t, (s, c, i) in enumerate([(100, 60, 3), (110, 66, 3)]):
        b.post(Posting(t, "run", "cash", "sales", s, {"cash": 1, "sales": 1}, "t", f"s{t}"))
        b.post(Posting(t, "run", "cogs", "cash", c, {"cogs": 1, "cash": -1}, "t", f"c{t}"))
        b.post(Posting(t, "run", "interest", "cash", i, {"interest": 1, "cash": -1}, "t", f"i{t}"))
        b.close_period(t)
    return b


PL = Scheme("pl", {"sales": "ebitda", "cogs": "ebitda", "interest": "financing"})
BS = Scheme("bs", {"cash": "cash", "debt": "debt", "equity": "equity"})


def test_a_pnl_cut_is_income_less_expense_by_period():
    assert pnl_cut(pl_book(), PL, ["ebitda"]).values == [40, 44]
    assert pnl_cut(pl_book(), PL, ["ebitda", "financing"]).values == [37, 41]


def test_a_balance_cut_counts_claims_positive_and_assets_negative():
    # net debt: debt less cash, from the opening state on
    assert balance_cut(pl_book(), BS, ["debt", "cash"]).values == [30 - 87, 30 - 128]


def test_a_pnl_line_left_out_of_the_scheme_refuses():
    with pytest.raises(CutError, match="interest"):
        pnl_cut(pl_book(), Scheme("pl", {"sales": "ebitda", "cogs": "ebitda"}), ["ebitda"])


def test_a_balance_left_out_of_the_scheme_refuses():
    with pytest.raises(CutError, match="equity"):
        balance_cut(pl_book(), Scheme("bs", {"cash": "cash", "debt": "debt"}), ["debt"])


def test_a_sum_cut_adds_and_subtracts_earlier_cuts_across_books():
    # a stream two books make together (a company's CFADS less a plan's capex, for one IRR): each part is
    # read where it is booked and the sum is a view, posting nothing
    import copy
    import pathlib

    from one_engine.cases import run_case
    from one_engine.inputs import InputError, load_yaml_strict
    case = load_yaml_strict((pathlib.Path(__file__).resolve().parents[1] / "cases" / "averta_spv.yaml").read_text())
    case["cuts"]["margin"] = {"kind": "sum", "add": ["revenue"], "subtract": ["opex"]}
    out = run_case(copy.deepcopy(case))
    rev, opex = out.cut("revenue").values, out.cut("opex").values
    assert out.cut("margin").values == [r - o for r, o in zip(rev, opex)]
    bad = copy.deepcopy(case)
    bad["cuts"]["margin"] = {"kind": "sum", "add": ["later"]}
    with pytest.raises(InputError, match="'later', which is not a cut declared above"):
        run_case(bad)
