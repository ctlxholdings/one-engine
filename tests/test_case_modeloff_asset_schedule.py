"""ModelOff 2012 "Asset Schedule": book and tax depreciation and deferred tax (plan step 7i).

Referee: the competition's five verified multiple-choice answers, with the
inputs, as transcribed from its paper in
``generic_engine/tests/test_benchmark_round1_asset_schedule.py`` and copied
to ``cases/reference/modeloff_2012_asset_schedule.json`` by
``consolidation/experiments/make_reference_modeloff_asset_schedule.py``
(which reads that module's constants and checks the answers against its
docstring). The paper itself is not in the repository, so the transcription
is not re-checked against it here. The answers are stated to one decimal
of US$ millions; one_engine must round to exactly them. Each is also
checked exactly against a closed form computed here from the inputs, so a
rounding coincidence cannot pass.

What the case adds to the engine (convergence count 1): ``Measurement``
gains declining-balance depreciation (``Policy.declining_rate``). Pack
changes (not core): the asset register reads that policy for either
register; ``income_tax`` takes its rate by period, may decline to recognise
a deferred tax asset on losses (``deferred.recognise_losses``), and records
the carrying amount, the tax base and the part of the deferred movement due
to a change of rate. Reused unchanged: the capex contract, the asset
register's tranches, the timing scheme of the income tax and the cash flow
cut.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_asset_schedule.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2012_asset_schedule.json").read_text())
I, A = REF["inputs"], REF["answers"]
REAL = {int(k): v for k, v in I["real_capex_2012_dollars"].items()}
YEARS = sorted(REAL)
N = len(YEARS)
Y = YEARS.index
NOMINAL = [REAL[y] * (1 + F(str(I["inflation"]))) ** (y - I["base_year"]) for y in YEARS]
LIFE, DB = I["book_life_years"], F(str(I["tax_declining_rate"]))
RATE = [F(str(I["tax_rate"]["to_2015"])) if y <= 2015 else F(str(I["tax_rate"]["from_2016"])) for y in YEARS]


def book_nbv(t):
    """The paper's book NBV at the end of period ``t``: each tranche less a twelfth of it a year, from the year bought."""
    return sum((c * max(0, 1 - F(t - k + 1, LIFE)) for k, c in enumerate(NOMINAL[:t + 1])), F(0))


def tax_nbv(t):
    """The paper's tax NBV at the end of period ``t``: each tranche times 0.6 for each year from the year bought."""
    return sum((c * (1 - DB) ** (t - k + 1) for k, c in enumerate(NOMINAL[:t + 1])), F(0))


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_inputs():
    capex, rb = CASE["contracts"]["capex"]["terms"], CASE["rulebooks"]
    assert [F(x) for x in capex["schedule"]] == NOMINAL
    a, tax = rb["assets"], rb["tax"]
    assert a["in_service"] == list(range(N)) and a["first_charge"] == "in_service"
    assert a["life_periods"] == LIFE
    assert a["tax"]["life_periods"] is None and a["tax"]["first_charge"] == "in_service"
    assert F(a["tax"]["declining_rate"]) == DB and set(a["tax"]) == {"life_periods", "declining_rate", "first_charge"}
    assert [F(x) for x in tax["rate"]] == RATE and RATE[Y(2015)] == F(3, 10) and RATE[Y(2016)] == F(28, 100)
    assert tax["deferred"]["recognise_losses"] is False
    assert YEARS[0] == 2012 and YEARS[-1] == 2029


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean(res):
    assert res.failed_checks() == []


def test_q1_nominal_capex_2012_to_2021(res):
    spent = -sum(res.cut("capex").values[Y(2012):Y(2021) + 1])
    assert spent == sum(NOMINAL[:Y(2021) + 1])
    assert round(float(spent), 1) == A["q1_nominal_capex_2012_2021"]


def test_q2_the_book_nbv_at_the_end_of_2017(res):
    nbv = res.group.book("company").balance("fixed_assets", Y(2017))
    assert nbv == book_nbv(Y(2017))
    assert round(float(nbv), 1) == A["q2_book_nbv_2017"]


def test_q3_the_tax_nbv_at_the_end_of_2019(res):
    w = res.records("tax")
    assert all(w[t]["tax_base"] == tax_nbv(t) for t in range(N - 1))
    assert round(float(w[Y(2019)]["tax_base"]), 1) == A["q3_tax_nbv_2019"]


def test_q4_the_rate_change_remeasures_the_opening_2016_deferred_tax(res):
    w = res.records("tax")
    change = w[Y(2016)]["rate_change"]
    assert change == (RATE[Y(2016)] - RATE[Y(2015)]) * (book_nbv(Y(2015)) - tax_nbv(Y(2015)))
    assert round(float(-change), 1) == A["q4_rate_change_impact_opening_2016"]   # a fall in the liability
    assert all(w[t]["rate_change"] == 0 for t in range(N) if t != Y(2016))


def test_q5_the_deferred_tax_liability_at_the_end_of_2028(res):
    dt = res.group.book("company").balance("deferred_tax", Y(2028))
    assert dt == RATE[Y(2028)] * (book_nbv(Y(2028)) - tax_nbv(Y(2028)))
    assert dt > 0 and A["q5_deferred_tax_2028_side"] == "liability"     # the account is liability-natured
    assert round(float(dt), 1) == A["q5_deferred_tax_2028"]


def test_each_years_deferred_movement_is_the_new_difference_at_the_new_rate_plus_the_remeasurement(res):
    book, w = res.group.book("company"), res.records("tax")
    for t in range(1, N):
        diff = w[t]["carrying"] - w[t]["tax_base"]
        prev = w[t - 1]["carrying"] - w[t - 1]["tax_base"]
        move = book.balance("deferred_tax", t) - book.opening("deferred_tax", t)
        assert move == RATE[t] * (diff - prev) + w[t]["rate_change"]


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_case_has_no_income_so_its_losses_carry_no_deferred_tax_asset(res):
    w = res.records("tax")
    assert all(w[t]["tax"] == 0 for t in range(N))
    assert w[Y(2028)]["losses"] == sum(tax_nbv(t - 1) * DB + NOMINAL[t] * DB if t else NOMINAL[0] * DB
                                       for t in range(Y(2028) + 1))
    # recognised, the losses would turn the 2028 liability into an asset
    def recognise(case):
        case["rulebooks"]["tax"]["deferred"]["recognise_losses"] = True
    other = variant(recognise)
    dt = other.group.book("company").balance("deferred_tax", Y(2028))
    assert dt == RATE[Y(2028)] * (book_nbv(Y(2028)) - tax_nbv(Y(2028)) - w[Y(2028)]["losses"]) and dt < 0


def test_finding_the_book_cost_is_gone_after_twelve_years_but_the_tax_balance_never_reaches_zero(res):
    book, w = res.group.book("company"), res.records("tax")
    def dep(t):
        return book.balance("depreciation", t) - book.opening("depreciation", t)
    # the 2012 tranche is charged a twelfth a year in 2012 to 2023, and nothing from 2024
    assert dep(Y(2023)) == sum(NOMINAL[:Y(2023) + 1]) / LIFE
    assert dep(Y(2024)) == sum(NOMINAL[Y(2013):Y(2024) + 1]) / LIFE
    assert w[Y(2028)]["tax_base"] > 0 and w[Y(2029)]["tax_base"] == 0          # written off at the end of the grid
    assert book.balance("fixed_assets", Y(2029)) == 0 and book.balance("deferred_tax", Y(2029)) == 0


def test_finding_the_capex_is_unfunded_so_cash_falls_by_every_dollar_spent(res):
    book = res.group.book("company")
    assert all(book.balance("cash", t) == -sum(NOMINAL[:t + 1]) for t in range(N))


# ── refusals ──────────────────────────────────────────────────────────────

def test_recognise_losses_takes_true_or_false():
    def word(case):
        case["rulebooks"]["tax"]["deferred"]["recognise_losses"] = "no"
    with pytest.raises(InputError, match="recognise_losses is true or false"):
        variant(word)


def test_a_tax_register_with_a_life_and_a_declining_rate_refuses():
    def both(case):
        case["rulebooks"]["assets"]["tax"]["life_periods"] = 5
    with pytest.raises(InputError, match="asset_register.tax: give life_periods, weights or declining_rate"):
        variant(both)


def test_a_rate_series_that_stops_short_refuses():
    def short(case):
        case["rulebooks"]["tax"]["rate"] = case["rulebooks"]["tax"]["rate"][:-1]
    with pytest.raises(ValueError, match=f"has no period {N - 1}"):
        variant(short)
