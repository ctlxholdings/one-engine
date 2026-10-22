"""ModelOff 2012 Finals "Buying an Investment Property": a mortgage swept by free cash, a sale each year (plan step 7j).

Referee: the competition's five verified multiple-choice answers, with the
inputs, as transcribed in
``generic_engine/tests/test_benchmark_round3_investment_property.py`` and
copied to ``cases/reference/modeloff_2012_investment_property.json`` by
``consolidation/experiments/make_reference_modeloff_investment_property.py``
(numbers read from that module's constants; the rest checked against its
docstring). The paper itself is not in the repository, so the transcription
is not re-checked against it here. The answers are stated to the dollar or
to a tenth of a percent; one_engine must round to exactly them. Each amount
is also checked against a closed form computed here from the inputs.

What the case adds to the engine (convergence count 0): no shared module
changed. Pack change (not core): the corporate pack's ``exit_returns`` lens
values an exit at a stated price by period (``value``) instead of a
multiple of EBITDA, and charges a capital gains tax on the price over a
cost read from the books (``gains_tax``). Reused unchanged: the capex,
offtake, operating cost, loan and sponsor equity contracts, the funding
plan, income tax, the waterfall's prepay tier, the lens's holder streams
and the ``irr`` measure.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_investment_property.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2012_investment_property.json").read_text())
I, A = REF["inputs"], REF["answers"]
M = I["mortgage"]
N = I["years"] + 1
PRICE = F(int(I["purchase_price"]))
COST = PRICE + F(int(I["furniture"])) + F(int(M["arrangement_fee"]))      # the capital gains cost: 1,620,000


def rent(y):
    """The paper's rent in year ``y``: 83,200 in year 1, +2% in years 2 and 3, +5% after."""
    x = F(0) if y == 0 else F(I["rent_year_1"])
    for k in range(2, y + 1):
        x *= 1 + F(str(I["rent_growth"]["years_2_3" if k <= 3 else "from_year_4"]))
    return x


def debt_after(y):
    """The paper's mortgage at the end of year ``y``: every year's cash after interest and tax repays it."""
    keep = 1 - F(str(I["agency_share_of_rent"])) - F(str(I["maintenance_share_of_rent"]))
    tax, rate = F(str(I["income_tax_rate"])), F(str(M["rate"]))
    d = F(int(M["max_debt"]))
    for k in range(1, y + 1):
        d -= (rent(k) * keep - d * rate) * (1 - tax)
    return d


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_inputs():
    c = CASE["contracts"]
    assert [F(x) for x in c["rent"]["terms"]["price"]] == [rent(y) for y in range(N)]
    assert [F(x) for x in c["agency"]["terms"]["volume"]] == [rent(y) for y in range(N)]
    assert F(c["agency"]["terms"]["unit_rate"]) == F(str(I["agency_share_of_rent"]))
    assert F(c["maintenance"]["terms"]["unit_rate"]) == F(str(I["maintenance_share_of_rent"]))
    assert c["maintenance"]["terms"]["volume"] == c["agency"]["terms"]["volume"]
    assert c["property"]["terms"]["schedule"] == [PRICE] and c["furniture"]["terms"]["schedule"] == [I["furniture"]]
    loan = c["mortgage"]["terms"]
    assert loan["commitment"] == M["max_debt"] and F(loan["rate"]) == F(str(M["rate"]))
    assert loan["arrangement_fee"] == M["arrangement_fee"] and loan["repay_periods"] == M["term_years"]
    assert loan["principal_schedule"] == [0] * N
    value = CASE["lenses"]["sale_1"]["value"]
    for k in range(N):
        assert float(value[k]) == pytest.approx(float(PRICE) * 2 ** (k / I["doubling_years"]), rel=1e-15)
    assert F(value[I["doubling_years"]]) == 2 * PRICE
    assert [CASE["lenses"][f"sale_{k}"]["period"] for k in range(1, N)] == list(range(1, N))
    assert F(CASE["lenses"]["sale_1"]["gains_tax"]["rate"]) == F(str(I["cgt_rate"]))
    assert F(CASE["rulebooks"]["tax"]["rate"]) == F(str(I["income_tax_rate"]))


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_equity_pays_what_the_mortgage_does_not(res):
    assert res.failed_checks() == []
    assert res.cut("owner").values[0] == COST - M["max_debt"] == 720_000
    assert all(x == 0 for x in res.cut("owner").values[1:])          # nothing is paid out before a sale
    book = res.group.book("company")
    assert all(book.balance("cash", t) == 0 for t in range(N))


def test_q1_the_cash_for_debt_service_in_year_1(res):
    paid = res.records("waterfall")[1]["paid"]["mortgage.prepay"]
    assert paid == debt_after(0) - debt_after(1)
    assert round(paid) == A["q1_cash_for_debt_service_year_1"]


def test_q2_the_mortgage_at_the_end_of_year_4(res):
    debt = res.group.book("company").balance("mortgage.debt", 4)
    assert debt == debt_after(4)
    assert round(debt) == A["q2_closing_debt_year_4"]


def test_q3_the_irr_of_a_sale_at_the_end_of_year_10(res):
    assert round(float(res.measures["irr_10"]) * 100, 1) == A["q3_irr_sold_year_10_pct"]


def test_q4_the_proceeds_of_a_sale_after_two_years(res):
    sale = res.lenses["sale_2"]
    value = F(CASE["lenses"]["sale_1"]["value"][2])
    cgt = F(str(I["cgt_rate"])) * (value - COST)
    assert sale["gains_tax"] == cgt and sale["net_debt"] == debt_after(2)
    assert sale["equity_value"] == value - cgt - debt_after(2)
    assert round(sale["equity_value"]) == A["q4_net_proceeds_sold_year_2"]
    assert res.cut("sale_2.owner").values == [-720_000, 0, sale["equity_value"]] + [0] * (N - 3)


def test_q5_the_year_whose_sale_has_the_highest_irr(res):
    irr = {k: res.measures[f"irr_{k}"] for k in range(1, N)}
    assert max(irr, key=irr.get) == A["q5_year_with_highest_irr"]
    # rising to year 2, falling after
    assert irr[1] < irr[2] and all(irr[k] > irr[k + 1] for k in range(2, N - 1))


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_papers_cash_for_debt_service_is_after_interest(res):
    w = res.records("waterfall")[1]
    # CFADS in the usual sense (after tax, before interest) is the interest larger than Q1's answer
    assert w["cfads"] == w["paid"]["mortgage.prepay"] + w["paid"]["mortgage.interest"]
    assert w["paid"]["mortgage.interest"] == F(int(M["max_debt"])) * F(str(M["rate"])) == 45_000


def test_finding_the_arrangement_fee_is_not_deducted_for_income_tax(res):
    book, tax = res.group.book("company"), res.records("tax")
    keep = 1 - F(str(I["agency_share_of_rent"])) - F(str(I["maintenance_share_of_rent"]))
    for t in range(1, N):
        assert tax[t]["base"] == rent(t) * keep - debt_after(t - 1) * F(str(M["rate"]))
    # it is amortised in the books over the 25-year term, 400 a year, outside the base
    assert book.balance("mortgage.fees", 1) - book.opening("mortgage.fees", 1) == 400


def test_finding_every_sale_is_at_a_gain_and_a_sale_below_cost_pays_no_gains_tax(res):
    assert all(res.lenses[f"sale_{k}"]["gains_tax"] > 0 for k in range(1, N))
    def cheap(case):
        for k in range(1, N):
            case["lenses"][f"sale_{k}"]["value"] = [1_000_000] * N
    sale = variant(cheap).lenses["sale_2"]
    assert sale["gains_tax"] == 0 and sale["equity_value"] == 1_000_000 - debt_after(2)


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_sale_valued_both_at_a_price_and_at_a_multiple_refuses():
    def both(case):
        case["lenses"]["sale_1"]["multiple"] = 10
    with pytest.raises(InputError, match="give multiple \\(with ebitda\\) or value, exactly one"):
        variant(both)


def test_a_sale_with_no_valuation_refuses():
    def neither(case):
        for k in range(1, N):
            del case["lenses"][f"sale_{k}"]["value"]
    with pytest.raises(InputError, match="exactly one"):
        variant(neither)
