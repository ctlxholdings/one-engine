"""Reproduction test: esco-product's firm layer (firm A, firm B, its treatment templates and its origination simulation) run on one_engine matches esco-product exactly.

Where the numbers come from
---------------------------
esco-product reads the ESCO firm in two ways (``engine/oe/firm``):

* firm A, Ameresco's FY2025 10-K: the 31 December 2025 balance sheet derived
  as the 2024 one plus the printed movements esco-product maps to each line,
  compared with the printed 2025 line by line (34 lines and groups, 12
  reconciled, the rest printing their difference);
* firm B, the model ESCO: the ESCO's legs of six EUR deals posted under named
  rules and read back as statements at each 31 December, the firm and each
  deal on its own; each deal under nine alternative treatments (13 run, 41
  not computed for want of a price or a sale); and an origination simulation
  (seven runs on each of two mixes of deals, and two grids of 40 and 42 runs).

``cases/reference/esco_engine/firm_layer.json`` (hash-pinned) holds what the
firm layer reads (the deals' dated legs reduced to leg, payer, payee, date
and amount; the deal map; the settings; the chart; the three settings
borrowed from Ameresco's printed lines, as exact fractions; firm A's lines
with their mapped movements) and what it produced, exact.

What is compared
----------------
* Firm A: every line's derived 2025 balance and its difference from the
  printed one; the clearing account closes at zero (the mapped movements are
  double-sided).
* Every firm B book (the firm, six deals, 13 templates, 14 simulation runs):
  each year's balance sheet, income statement, direct cash flow by section,
  indirect operating cash flow line by line, the four checks' differences and
  working capital, all read from one_engine's book; the postings themselves
  (in order, for the books and templates; by digest and count for the
  simulation runs).
* Templates: the rate each one finds, and the reason a template is not
  computed.
* Simulation: every yearly row (cash, equity, debt, covenant EBITDA, interest
  cover, leverage on both bases, the limits binding, staff, copies not
  originated), the equity bridge, the number of candidate copies; and for
  both grids each run's first binding year and limit on both bases, peak debt
  and lowest equity, or cumulative net income and loss years.

Translation: esco-product names three settings by where they come from
(``ameresco_weighted_rate``, ``ameresco_europe_sga_ratio``,
``ameresco_europe_gross_margin``); here the settings carry the numbers,
which the capture took from esco-product's own reading of Ameresco's lines.

If this fails, the engine or the translation is wrong, never the number.
"""
import copy
import hashlib
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError
from one_engine.oracles import verify
from one_engine.packs import firm as FM

REFERENCE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference"
REL = "esco_engine/firm_layer.json"


def load():
    verify(REL)
    return json.loads((REFERENCE / REL).read_text())


REF = load()
I, R = REF["inputs"], REF["runs"]
R2A = {f: [dict(zip(("leg", "payer", "payee", "date", "amount"), x)) for x in rows] for f, rows in I["r2a"].items()}
DEALS = I["deals"]["deals"]
CHART = I["chart"]
B = {k: F(v) for k, v in I["borrowed"].items()}


def settings():
    """esco-product's settings with its three named values replaced by the numbers they name."""
    s = copy.deepcopy(I["settings"])
    if s["debt_rate"] == "ameresco_weighted_rate":
        s["debt_rate"] = str(B["rate"])
    if s["overhead"] == "ameresco_europe_sga_ratio":
        s["overhead"] = str(B["sga_ratio"])
    w = s.pop("works_cost_where_not_printed")
    s["works_cost_margin"] = str(B["margin"]) if w == "ameresco_europe_gross_margin" else w
    return s


S0 = settings()
MIXES = {k: (S0["projects_per_year"] if v == "projects_per_year" else {d: v.get(d, 0) for d in S0["projects_per_year"]})
         for k, v in S0["mixes"].items()}


def case(rb, periods):
    return {"case": "firm", "periods": periods, "phases": ["settle"], "currency": "EUR", "floors": {}, "generators": {},
            "contracts": {}, "schemes": {}, "cuts": {}, "measures": {}, "rulebooks": {"firm": rb}}


def book(spec, years):
    rb = {"kind": "firm_book", "entity": "esco", "phase": "settle", "chart": CHART, "deals": DEALS, "r2a": R2A,
          "period_ends": years, "book": spec}
    res = run_case(case(rb, len(years)))
    assert res.failed_checks() == []
    return res.records("firm")


def canon(ps):
    return [[p.date, p.rule, p.deal, sorted([a, str(F(v))] for a, v in p.legs.items())] for p in ps]


def assert_statements(recs, exp):
    for t, y in enumerate(exp["years"]):
        r = recs[t]
        assert r["balance"] == {a: F(v) for a, v in exp["balance"][y].items()}, y
        assert r["income"] == {a: F(v) for a, v in exp["income"][y].items()}, y
        assert r["cash_direct"] == {a: F(v) for a, v in exp["cash_direct"][y].items()}, y
        assert r["indirect"] == [(n, F(v)) for n, v in exp["indirect"][y]], y
        assert [d for _, d in r["checks"]] == [F(d) for _, _, d in exp["checks"][y]], y
        assert all(d == 0 for _, d in r["checks"]), y
        assert r["working_capital"] == F(exp["wc"][y]), y


# ── firm A ───────────────────────────────────────────────────────────────────

def firm_a(lines):
    return run_case(case({"kind": "rollforward", "entity": "firm_a", "phase": "settle", "lines": lines}, 1))


def test_firm_a_derived_balance_sheet():
    res = firm_a(I["firm_a"])
    assert res.failed_checks() == []
    rec = res.records("firm")[0]
    assert rec["clearing"] == 0
    assert set(rec["lines"]) == set(R["firm_a"])
    for line, e in R["firm_a"].items():
        assert rec["lines"][line]["derived"] == F(e["derived"]), line
        assert rec["lines"][line]["difference"] == F(e["difference"]), line
    assert sum(1 for e in R["firm_a"].values() if F(e["difference"]) == 0) == 12


def test_firm_a_one_sided_movement_leaves_the_clearing_account_open():
    lines = copy.deepcopy(I["firm_a"])
    cash = next(x for x in lines if x["line"] == "cash_and_restricted_cash")
    cash["movements"].append({"ref": "test", "amount": "5", "why": "a movement mapped to one side only"})
    assert firm_a(lines).records("firm")[0]["clearing"] == 5


def test_firm_a_refuses_an_unbalanced_opening():
    lines = copy.deepcopy(I["firm_a"])
    lines[0]["opening"] = str(F(lines[0]["opening"]) + 1)
    with pytest.raises(InputError, match="do not balance"):
        firm_a(lines)


# ── firm B: base books ───────────────────────────────────────────────────────

@pytest.mark.parametrize("label", ["firm"] + sorted(DEALS))
def test_base_book(label):
    exp = R["base"][label]
    ps, _ = FM.base_postings(DEALS, R2A)
    if label != "firm":
        ps = [p for p in ps if p.deal == label]
    assert canon(FM.fund(ps, "firm_b" if label == "firm" else label)) == exp["postings"]
    recs = book({"kind": "base"} if label == "firm" else {"kind": "base", "deal": label}, exp["years"])
    assert_statements(recs, exp)


def test_base_not_posted():
    _, skipped = FM.base_postings(DEALS, R2A)
    assert [(x["deal"], x["leg"]) for x in skipped] == [(x["deal"], x["leg"]) for x in R["base"]["not_posted"]]


# ── firm B: templates ────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", sorted(R["templates"]))
def test_template(key):
    name, deal = key.split("|")
    exp = R["templates"][key]
    tps, why, rt = FM.template_postings(name, deal, DEALS[deal], R2A)
    if "not_computed" in exp:
        assert tps is None and why == exp["not_computed"]
        return
    assert rt == (None if exp["rate"] is None else F(exp["rate"]))
    assert canon(FM.fund(tps, key)) == exp["postings"]
    assert_statements(book({"kind": "template", "template": name, "deal": deal}, exp["years"]), exp)


def test_template_counts():
    run = [k for k, v in R["templates"].items() if "not_computed" not in v]
    assert len(run) == 13 and len(R["templates"]) == 9 * 6


# ── firm B: simulation ───────────────────────────────────────────────────────

def same(got, want):
    """A captured row value (fraction string, int, None, list of strings) against the computed one."""
    if isinstance(want, str):
        return got == F(want)
    return got == want


@pytest.mark.parametrize("key", sorted(R["simulation"]))
def test_simulation_run(key):
    mx, name = key.split("|")
    exp = R["simulation"][key]
    s = copy.deepcopy(S0)
    s["projects_per_year"] = MIXES[mx]
    stress = None if name == "base" else name
    ps, rows, _ = FM.simulate(DEALS, R2A, s, CHART, stress)
    assert len(ps) == exp["n"]
    assert hashlib.sha256(json.dumps(canon(ps)).encode()).hexdigest() == exp["sha"]
    assert len(rows) == len(exp["rows"])
    for got, want in zip(rows, exp["rows"]):
        bad = [k for k in want if not same(got[k], want[k])]
        assert not bad, (got["year"], bad)
    assert FM.equity_bridge(ps, CHART) == {k: F(v) for k, v in exp["bridge"].items()}
    assert len(FM.candidates(DEALS, R2A, s, stress)) == exp["candidates"]
    recs = book({"kind": "simulation", "settings": s, "stress": stress}, exp["book"]["years"])
    assert_statements(recs, exp["book"])
    assert [recs[t]["metrics"]["year"] for t in range(len(recs))] == [int(y[:4]) for y in exp["book"]["years"]]
    assert recs[len(recs) - 1]["bridge"] == FM.equity_bridge(ps, CHART)


def test_simulation_binds_a_limit_in_the_base_run():
    rows = R["simulation"]["default|base"]["rows"]
    assert any(r["binds"] for r in rows)


def test_grids():
    gi, gv = iter(R["grids"]["origination"]), iter(R["grids"]["volume"])
    for mx, base in MIXES.items():
        for k in S0["grids"]["origination_multiplier"]:
            for eq in S0["grids"]["equity_paid_in"]:
                s = copy.deepcopy(S0)
                s["projects_per_year"] = {d: v * k for d, v in base.items()}
                s["equity_paid_in"] = eq
                _, rows, _ = FM.simulate(DEALS, R2A, s, CHART)
                e = next(gi)
                assert (e["mix"], e["k"], e["equity"]) == (mx, k, eq)
                assert [list(FM.first_bind(rows)), list(FM.first_bind(rows, "recourse"))] == [e["all"], e["recourse"]]
                assert max(r["debt"] for r in rows) == F(e["peak_debt"])
                assert min(r["equity"] for r in rows) == F(e["low_equity"])
        for fc in S0["grids"]["fixed_cost"]:
            for k in S0["grids"]["volume_multiplier"]:
                s = copy.deepcopy(S0)
                s["projects_per_year"] = {d: v * k for d, v in base.items()}
                s["overhead"], s["fixed_cost"] = 0, fc
                _, rows, _ = FM.simulate(DEALS, R2A, s, CHART)
                e = next(gv)
                assert (e["mix"], e["k"], e["fixed_cost"]) == (mx, k, fc)
                assert sum((r["net_income"] for r in rows), F(0)) == F(e["cum_ni"])
                assert sum(1 for r in rows if r["net_income"] < 0) == e["neg_years"]
    assert next(gi, None) is None and next(gv, None) is None


# ── units of the pack ────────────────────────────────────────────────────────

def test_pairs_split_a_three_leg_posting():
    assert FM._pairs({"cash": F(90), "loss_on_derecognition": F(10), "financing_receivable": F(-100)}) == [
        ("cash", "financing_receivable", F(90)), ("loss_on_derecognition", "financing_receivable", F(10))]
    assert FM._pairs({"cash": F(5), "receivable_sold_not_derecognised": F(7), "liability_to_forfaiter": F(-5),
                      "financing_receivable": F(-7)}) == [
        ("cash", "liability_to_forfaiter", F(5)), ("receivable_sold_not_derecognised", "financing_receivable", F(7))]
    assert FM._pairs({"cash": F(100), "borrowings": F(-60), "share_capital": F(-40)}) == [
        ("cash", "borrowings", F(60)), ("cash", "share_capital", F(40))]


def test_equity_bridge_refuses_an_account_outside_its_lines():
    chart = dict(CHART, bonus={"statement": "income_statement", "line": "operating expenses", "normal_balance": "debit"})
    ps = [FM.FirmPosting("2020-01-01", "E1", {"cash": F(10), "share_capital": F(-10)}),
          FM.FirmPosting("2020-06-30", "W4-other", {"bonus": F(1), "cash": F(-1)})]
    with pytest.raises(InputError, match="not in the equity bridge"):
        FM.equity_bridge(ps, chart)


def test_fund_books_the_shortfall_and_nothing_else():
    ps = [FM.FirmPosting("2020-01-01", "T1-works", {"cost_of_construction": F(100), "cash": F(-100)}),
          FM.FirmPosting("2021-01-01", "T1-fee", {"cash": F(30), "trade_receivable": F(-30)})]
    out = FM.fund(ps, "x")
    assert [(p.date, p.rule, p.legs) for p in out[2:]] == [
        ("2020-01-01", "F1-fund", {"cash": F(100), "funding_not_printed": F(-100)})]


def test_ledger_refuses_unbalanced_and_unknown():
    with pytest.raises(InputError, match="does not balance"):
        FM.Ledger([FM.FirmPosting("2020-01-01", "C1", {"cash": F(1), "borrowings": F(-2)})], CHART)
    with pytest.raises(InputError, match="unknown accounts"):
        FM.Ledger([FM.FirmPosting("2020-01-01", "C1", {"cash": F(1), "nowhere": F(-1)})], CHART)


def test_cash_posting_needs_a_cash_flow_section():
    bk = FM.Ledger([FM.FirmPosting("2020-01-01", "XX", {"cash": F(1), "borrowings": F(-1)})], CHART)
    with pytest.raises(InputError, match="no cash-flow section"):
        bk.cash_flow_direct(None, "2020-12-31")


def test_refuses_unknown_template_stress_and_kind():
    with pytest.raises(InputError, match="unknown template"):
        FM.template_postings("leaseback", "austria_pharma_lease", DEALS["austria_pharma_lease"], R2A)
    with pytest.raises(InputError, match="unknown stress"):
        FM.simulate(DEALS, R2A, S0, CHART, "war")
    with pytest.raises(InputError, match="book kind"):
        book({"kind": "budget"}, ["2020-12-31"])


def test_refuses_a_posting_after_the_last_period():
    years = R["base"]["firm"]["years"]
    with pytest.raises(InputError, match="after the last period end"):
        book({"kind": "base"}, years[:-1])


def test_refuses_a_leg_without_a_kind():
    deals = copy.deepcopy(DEALS)
    del deals["bytom_schools"]["legs"]["c2"]
    with pytest.raises(InputError, match="has no kind"):
        FM.base_postings(deals, R2A)


def test_rate_equates_price_and_payments():
    pays = [("2021-01-01", F(60)), ("2022-01-01", F(60))]
    r = FM.rate(F(100), "2020-01-01", pays)
    assert abs(sum(a / (1 + r) ** (int(d[:4]) - 2020) for d, a in pays) - 100) < F(1, 10 ** 9)
    assert FM.rate(F(100), "2020-01-01", [("2020-01-01", F(100))]) is None
    assert FM.rate(F(100), "2020-01-01", [("2021-01-01", F(100))]) == 0
