"""ModelOff 2017 "Collect the Cash": a daily cash forecast of eight customers (plan step 7q).

Referee: the competition's ten answers, with the customers' payment rules,
as transcribed in
``generic_engine/tests/test_benchmark_round3_collect_the_cash.py`` and copied
to ``cases/reference/modeloff_2017_collect_the_cash.json`` by
``consolidation/experiments/make_reference_modeloff_collect_the_cash.py``
(each rule and answer checked against that module's text). The paper itself
is not in the repository, so the transcription is not re-checked against it
here. The answers are stated to the cent of a million or as a count;
one_engine must round to exactly them. All ten are reproduced, Q10 included,
which the transcription misses by 0.36m (see the finding below).

The payment days are derived by the case generator with one_engine.dates;
this test derives them again with plain calendar arithmetic (``days_of``),
so a fault in either shows as a difference.

What the case adds to the engine (convergence count 1): Dates names a day
of a month by its rank (``nth_weekday``: the third Thursday;
``nth_business_day``: the third weekday). Pack change (not core): an
``offtake_on_dates`` contract accrues a sale every day and collects
everything owed on stated days. Reused unchanged: the offtake with a
payment profile (step 7l) or paid the same day, the opening balance sheet,
the weekday calendar and the preceding convention, cycles, the cash flow
cut.
"""
import calendar
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_collect_the_cash.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2017_collect_the_cash.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = CASE["periods"]
START = dt.date(2018, 1, 1)
NAMES = "abcdefgh"


def day(t):
    return START + dt.timedelta(days=t)


def period(d):
    return (d - START).days


def days_of(month, year=2018):
    return [dt.date(year, month, k) for k in range(1, calendar.monthrange(year, month)[1] + 1)]


def weekday(d):
    return d.weekday() < 5


def third_thursday(year, month):
    return [d for d in days_of(month, year) if d.weekday() == 3][2]


def received(res, name):
    """Cash received from a customer, by day."""
    return res.cut(name).values


def window(res, name, first, last):
    return sum(x for t, x in enumerate(received(res, name)) if first <= day(t) <= last)


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


# ── the copy of the inputs and the days ───────────────────────────────────

def test_the_case_file_carries_the_papers_payment_days():
    c = {k: CASE["contracts"][k]["terms"] for k in NAMES}
    months = range(1, 13)
    assert c["b"]["collect_on"] == [period(third_thursday(2018, m)) for m in months]
    assert c["c"]["collect_on"] == [t for t in range(N) if weekday(day(t))]
    friday_before = lambda d: d - dt.timedelta(days=max(0, d.weekday() - 4))
    f = [friday_before(dt.date(2018, m, k)) for m in months for k in (7, 14, 20)]
    assert [t for t in range(N) if c["f"]["volume"][t]] == sorted(period(d) for d in f)
    g = [[d for d in days_of(m) if weekday(d)][0 if m % 2 == 0 else 2] for m in months]
    assert [t for t in range(N) if c["g"]["volume"][t]] == [period(d) for d in g]
    h = []
    for m in months:
        last = days_of(m)[-1].day
        h += [w + dt.timedelta(days=-1 if w.day <= 10 else 1 if w.day > last - 10 else 0)
              for w in days_of(m) if w.weekday() == 2]
    assert [t for t in range(N) if c["h"]["volume"][t]] == sorted(period(d) for d in h)
    for m in months:
        tuesdays, weekdays = sum(d.weekday() == 1 for d in days_of(m)), [d for d in days_of(m) if weekday(d)]
        assert {F(c["e"]["price"][period(d)]) for d in weekdays} == {F("1.96") * tuesdays / len(weekdays)}
        assert c["d"]["volume"][period(dt.date(2018, m, 1))] == len(days_of(m))
    # A is invoiced on the 1st: the 5th is four days on
    profile = [F(0)] * 23
    for q in I["a"]["paid"]:
        profile[q["day"] - 1] = F(q["share"])
    assert [F(x) for x in c["a"]["payment_days"]] == profile
    assert [t for t in range(N) if c["a"]["volume"][t]] == [period(dt.date(2018, m, 1)) for m in months]


def test_what_b_and_c_had_accrued_in_2017_is_owed_at_the_start():
    o = CASE["opening"]["company"]
    # B: the days after December 2017's third Thursday (the 21st); C: the weekend of 30 and 31 December
    assert F(o["b.receivable"]["value"]) == F("0.35") * (dt.date(2017, 12, 31) - third_thursday(2017, 12)).days
    assert F(o["c.receivable"]["value"]) == F("0.18") * 2 and dt.date(2017, 12, 30).weekday() == 5


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean(res):
    assert res.failed_checks() == []


def test_q1_a_from_7_march_to_18_october(res):
    assert round(float(window(res, "a", dt.date(2018, 3, 7), dt.date(2018, 10, 18))), 2) == A["q1_a_7_mar_to_18_oct"]


def test_q2_b_from_april_to_october(res):
    assert round(float(window(res, "b", dt.date(2018, 4, 1), dt.date(2018, 10, 31))), 2) == A["q2_b_apr_to_oct"]


def test_q3_c_from_15_july_to_24_november(res):
    assert round(float(window(res, "c", dt.date(2018, 7, 15), dt.date(2018, 11, 24))), 2) == A["q3_c_15_jul_to_24_nov"]


def test_q4_d_from_april_to_october(res):
    assert round(float(window(res, "d", dt.date(2018, 4, 1), dt.date(2018, 10, 31))), 2) == A["q4_d_apr_to_oct"]


def test_q5_e_from_2_february_to_20_august(res):
    assert round(float(window(res, "e", dt.date(2018, 2, 2), dt.date(2018, 8, 20))), 2) == A["q5_e_2_feb_to_20_aug"]


def test_q6_f_on_fridays(res):
    x = sum(v for t, v in enumerate(received(res, "f")) if day(t).weekday() == 4)
    assert round(float(x), 2) == A["q6_f_on_fridays"]


def test_q7_how_often_g_pays_on_the_3rd(res):
    assert sum(1 for t, v in enumerate(received(res, "g")) if v and day(t).day == 3) == A["q7_g_times_on_the_3rd"]


def test_q8_h_on_tuesdays(res):
    x = sum(v for t, v in enumerate(received(res, "h")) if day(t).weekday() == 1)
    assert round(float(x), 2) == A["q8_h_on_tuesdays"]


def test_q9_everyone_from_1_to_10_september(res):
    assert round(float(window(res, "all", dt.date(2018, 9, 1), dt.date(2018, 9, 10))), 2) == A["q9_all_1_to_10_sep"]


def test_q10_everyone_in_2018(res):
    total = sum(received(res, "all"))
    assert total == sum(sum(received(res, k)) for k in NAMES)
    assert round(float(total), 2) == A["q10_all_2018"]


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_transcriptions_q10_gap_is_cs_weekend_before_2018(res):
    # without C's 0.36 owed for 30-31 December 2017, the year's total is 982.65, the transcription's figure;
    # its note put the gap on B's accrual window instead
    def no_weekend(case):
        case["opening"]["company"]["c.receivable"]["value"] = 0
    total = sum(received(variant(no_weekend), "all"))
    assert round(float(total), 2) == 982.65 and round(float(sum(received(res, "all")) - total), 2) == 0.36
    assert received(res, "c")[0] == F("0.18") * 3                    # Monday 1 January pays three days


def test_finding_b_and_c_end_the_year_owed_what_accrued_after_their_last_payment(res):
    book = res.group.book("company")
    assert book.balance("b.receivable", N - 1) == F("0.35") * (dt.date(2018, 12, 31) - third_thursday(2018, 12)).days
    assert book.balance("c.receivable", N - 1) == 0                   # 31 December 2018 is a Monday
