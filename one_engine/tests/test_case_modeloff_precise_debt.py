"""ModelOff 2014 "Precise Debt Modeling": two loans with business-day payment dates (plan step 7m).

Referee: the competition's eight multiple-choice answers, with the holidays
and the loans' terms, as transcribed in
``generic_engine/tests/test_benchmark_round1_precise_debt.py`` and copied to
``cases/reference/modeloff_2014_precise_debt.json`` by
``consolidation/experiments/make_reference_modeloff_precise_debt.py``
(holidays and terms read from that module's constants; the conditions and
answers checked against its text). The paper itself is not in the
repository, so the transcription is not re-checked against it here. The
answers are counts, a period, an amount "closest to" a choice, cents and a
rounded percentage; one_engine must give exactly them.

What the case adds to the engine (convergence count 1): ``Dates`` takes a
holiday list on the weekday calendar (``is_business_day``, and ``shift``
skips holidays). Pack change (not core): an ``annuity`` loop rule sets a
loan's principal schedule for a constant payment on its own days per
period. Reused unchanged: the loan contract (a draw at a stated period,
interest on actual days over 365, a principal schedule), the modified
following convention, loops and ``pay_as_due`` (step 7l).
"""
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.dates import CalendarError, Cycle, instant, is_business_day, shift
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_precise_debt.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2014_precise_debt.json").read_text())
I, A = REF["inputs"], REF["answers"]
HOLIDAYS = frozenset(dt.date.fromisoformat(d) for d in I["holidays"])
N = CASE["periods"]


def period(d):
    return (d.year - 2015) * 12 + d.month - 1


def dates(name):
    """The paper's regular and actual payment dates of a loan, derived with one_engine.dates."""
    loan = I[name]
    draw = instant(loan["drawdown"])
    regular = [Cycle.parse("P1ML1").step(draw, k, False) for k in range(1, loan["months"] + 1)]
    return draw, regular, [shift(r, "SCMF", "MF", HOLIDAYS)[0] for r in regular]


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def payment(book, loan, t):
    """Interest plus principal paid in ``t``."""
    return mv(book, f"{loan}.interest", t) - mv(book, f"{loan}.debt", t)


def window(name):
    draw, _, actual = dates(name)
    return range(period(actual[0].date()), period(actual[-1].date()) + 1)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("company")


# ── the copy of the inputs and the dates ──────────────────────────────────

def test_the_case_file_carries_the_actual_days_between_payment_dates():
    for name in ("loan_1", "loan_2"):
        loan, terms = I[name], CASE["contracts"][name]["terms"]
        draw, _, actual = dates(name)
        expected = [0] * N
        prev = draw
        for a in actual:
            expected[period(a.date())] = (a - prev).days
            prev = a
        assert terms["days_per_period"] == expected
        assert terms["commitment"] == loan["amount"] and F(terms["rate"]) == F(str(loan["rate"]))
        assert terms["repay_periods"] == loan["months"] and terms["day_count_basis"] == 365
        assert terms["draw_schedule"][period(draw.date())] == loan["amount"]
    assert len(HOLIDAYS) == 18


def test_loan_1s_nineteenth_never_needs_the_month_end_fallback():
    # condition 1 alone (following, unless the month would change) is modified following here
    _, regular, actual = dates("loan_1")
    assert actual == [shift(r, "SCF", "MF", HOLIDAYS)[0] for r in regular]


def test_loan_2s_thirtieth_falls_back_to_the_last_business_day_in_february():
    _, regular, actual = dates("loan_2")
    feb = [(r, a) for r, a in zip(regular, actual) if r.month == 2]
    assert len(feb) == 4 and [r.day for r, _ in feb] == [29, 28, 28, 28]     # 2016 to 2019, clamped
    for r, a in feb:
        assert a.month == 2 and is_business_day(a, "MF", HOLIDAYS)
        assert all(not is_business_day(a + dt.timedelta(days=k), "MF", HOLIDAYS) for k in range(1, r.day - a.day + 1))


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_both_loops_close_on_their_second_run(res, book):
    assert res.failed_checks() == []
    for name in ("loan_1", "loan_2"):
        assert res.loops[f"{name}_payment"].iterations == 2 and res.loops[f"{name}_payment"].residual == 0
        assert book.balance(f"{name}.debt", window(name)[-1]) == 0


def test_q1_how_many_of_loan_1s_regular_dates_are_not_business_days():
    _, regular, _ = dates("loan_1")
    assert sum(not is_business_day(r, "MF", HOLIDAYS) for r in regular) == A["q1_loan_1_regular_dates_not_business_days"]


def test_q2_loan_1s_total_interest(book):
    total = sum(mv(book, "loan_1.interest", t) for t in range(N))
    # the paper asks for the closest of its choices (not transcribed): to the nearest ten dollars
    assert round(float(total), -1) == A["q2_loan_1_total_interest_closest_to"]


def test_q3_loan_1s_payment_ends_in_85_cents(book):
    pays = [payment(book, "loan_1", t) for t in window("loan_1")]
    # the same every month, the last included (to the float the loop carries its schedule in)
    assert float(max(pays) - min(pays)) < 1e-9
    p = pays[0]
    assert round(float(p) * 100) % 100 == A["q3_loan_1_payment_cents"]
    assert float(p) == pytest.approx(4049.8477507737707, abs=1e-9)   # the goal seek of the old test


def test_q4_the_first_payment_after_which_loan_1_is_below_40_percent(book):
    first = next(k for k, t in enumerate(window("loan_1"), 1) if book.balance("loan_1.debt", t) < F(4, 10) * 250_000)
    assert first == A["q4_loan_1_first_payment_below_40_pct"]


def test_q5_how_many_of_loan_2s_interest_periods_have_31_days():
    days = CASE["contracts"]["loan_2"]["terms"]["days_per_period"]
    assert sum(d == 31 for d in days) == A["q5_loan_2_periods_of_31_days"]


def test_q6_q7_q8_loan_2(book):
    pays = [payment(book, "loan_2", t) for t in window("loan_2")]
    assert float(max(pays) - min(pays)) < 1e-9
    p = pays[0]
    assert round(float(p), 2) == A["q6_loan_2_payment"]
    twelfth = window("loan_2")[11]
    assert round(float(book.balance("loan_2.debt", twelfth)), 2) == A["q7_loan_2_balance_after_12th_payment"]
    interest = sum(mv(book, "loan_2.interest", t) for t in range(N))
    assert round(float(interest / (p * I["loan_2"]["months"])) * 100) == A["q8_loan_2_interest_share_of_payments_pct"]


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_without_the_holidays_the_dates_and_the_payment_change():
    _, regular, actual = dates("loan_1")
    weekdays_only = [shift(r, "SCMF", "MF")[0] for r in regular]
    assert sum(a != w for a, w in zip(actual, weekdays_only)) > 0
    assert sum(not is_business_day(r, "MF") for r in regular) < A["q1_loan_1_regular_dates_not_business_days"]


def test_finding_the_annuity_on_actual_days_is_not_the_textbook_annuity(book):
    # a 30/360 annuity of 250,000 over 72 months at 5.2% pays less than the actual-days payment
    r = F(52, 1000) / 12
    textbook = 250_000 * r / (1 - (1 + r) ** -72)
    assert payment(book, "loan_1", 1) != textbook and abs(float(payment(book, "loan_1", 1) - textbook)) < 5


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_holiday_list_without_the_weekday_calendar_refuses():
    with pytest.raises(CalendarError, match="holiday"):
        is_business_day(instant("2015-04-03"), "NC", HOLIDAYS)
