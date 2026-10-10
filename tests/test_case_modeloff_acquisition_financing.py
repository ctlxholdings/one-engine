"""ModelOff 2013 "Acquisition Financing": a facility of four tranches, each repaid its own way (plan step 7n).

Referee: the competition's six answers, with the tranches' terms, as
transcribed in
``generic_engine/tests/test_benchmark_round2_acquisition_financing.py`` and
copied to ``cases/reference/modeloff_2013_acquisition_financing.json`` by
``consolidation/experiments/make_reference_modeloff_acquisition_financing.py``
(each term and answer checked against that module's text). The paper itself
is not in the repository, so the transcription is not re-checked against it
here. The answers are stated to the cent, to a hundredth of a percent and as
a date; one_engine must round to exactly them.

What the case adds to the engine (convergence count 0): no shared module
changed. Pack change (not core): a ``level`` loop rule sets a loan's
principal schedule to equal steps of what it owes when repayment starts
(principal plus capitalised interest), where the loan's own equal steps
are steps of its commitment. Reused unchanged: the loan contract (draws at
stated periods, a rate series, a capitalisation window, equal steps, a
bullet as one step, capitalised interest repaid ahead of principal), the
``annuity`` loop rule (step 7m) and ``pay_as_due`` (step 7l).
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_acquisition_financing.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2013_acquisition_financing.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = CASE["periods"]
TRANCHES = [f"tranche_{k}" for k in range(1, 5)]


def q(date):
    """The period of a quarter-end date: period 0 is the quarter ending 31 December 2013."""
    y, m = int(date[:4]), int(date[5:7])
    return (y - 2013) * 4 + (m - 12) // 3


def date(t):
    y, m = 2013 + (t + 3) // 4, 3 * ((t + 3) % 4 + 1)
    return f"{y}-{m:02d}-{30 if m in (6, 9) else 31}"


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def owed(book, loan, t):
    return book.balance(f"{loan}.debt", t) + book.balance(f"{loan}.capitalised_interest", t)


def paid(book, loan, t):
    """Cash paid to the loan in ``t``: what it owes fell by, plus the interest charged."""
    return owed(book, loan, t - 1) - owed(book, loan, t) + mv(book, f"{loan}.interest", t)


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("company")


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_terms():
    c = {k: CASE["contracts"][k]["terms"] for k in TRANCHES}
    t1, t2, t3, t4 = (I[k] for k in TRANCHES)
    for terms in c.values():
        assert terms["days_per_period"] == 90 and terms["day_count_basis"] == 360
    # tranche 1: drawn at the start, 28 quarters of equal payments
    assert c["tranche_1"]["draw_schedule"][q(t1["drawn"])] == c["tranche_1"]["commitment"] == t1["amount"]
    assert F(c["tranche_1"]["rate"]) == F(t1["rate"]) and c["tranche_1"]["repay_periods"] == t1["quarters"]
    # tranche 2: 9.00% for 20 quarters of interest only, then 8.50% for 16 equal steps
    rates = [F(x) for x in c["tranche_2"]["rate"]]
    io = t2["interest_only_quarters"]
    assert rates[1:io + 1] == [F(t2["rate_interest_only"])] * io
    assert rates[io + 1:] == [F(t2["rate_amortising"])] * t2["amortising_quarters"]
    assert c["tranche_2"]["repay_from"] == io + 1 and c["tranche_2"]["repay_periods"] == t2["amortising_quarters"]
    assert c["tranche_2"]["principal_schedule"] is None
    # tranche 3: two draws, a bullet at maturity
    assert {t: x for t, x in enumerate(c["tranche_3"]["draw_schedule"]) if x} == \
        {q(d["date"]): d["amount"] for d in t3["draws"]}
    assert c["tranche_3"]["repay_from"] == q(t3["maturity"]) and c["tranche_3"]["repay_periods"] == 1
    # tranche 4: 8 quarters capitalised at 8.20%, then 16 steps at 7.70% to maturity
    d4 = q(t4["drawn"])
    assert c["tranche_4"]["draw_schedule"][d4] == t4["amount"]
    assert (c["tranche_4"]["capitalise_from"], c["tranche_4"]["capitalise_until"]) == (d4 + 1, d4 + 8)
    rates = [F(x) for x in c["tranche_4"]["rate"]]
    assert set(rates[d4 + 1:d4 + 9]) == {F(t4["rate_capitalised"])} and set(rates[d4 + 9:]) == {F(t4["rate_repayment"])}
    assert c["tranche_4"]["repay_from"] + c["tranche_4"]["repay_periods"] - 1 == q(t4["maturity"])


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_both_loops_close_and_every_tranche_is_repaid(res, book):
    assert res.failed_checks() == []
    for loop in ("tranche_1_payment", "tranche_4_steps"):
        assert res.loops[loop].iterations == 2 and res.loops[loop].residual == 0
    for loan in TRANCHES:
        terms = CASE["contracts"][loan]["terms"]
        maturity = terms["repay_from"] + terms["repay_periods"] - 1
        assert owed(book, loan, maturity - 1) > 0 and abs(float(owed(book, loan, maturity))) < 1e-6


def test_q1_tranche_3s_total_interest(book):
    total = sum(mv(book, "tranche_3.interest", t) for t in range(N))
    # the second draw, at a quarter end, earns interest from the next quarter
    assert total == F(7450, 10 ** 5) / 4 * (10_000_000 * 16 + 4_000_000 * 13)
    assert round(float(total), 2) == A["q1_tranche_3_total_interest"]


def test_q2_tranche_1s_balance_at_31_march_2020(book):
    assert round(float(book.balance("tranche_1.debt", q("2020-03-31"))), 2) == A["q2_tranche_1_closing_2020_03_31"]


def test_q3_tranche_2s_debt_service_at_31_december_2020(book):
    t = q("2020-12-31")
    assert paid(book, "tranche_2", t) == F(25_000_000, 16) + F(25_000_000 * 9, 16) * F(85, 4000)
    # exactly 1,861,328.125: the paper rounds the half cent up (as a spreadsheet does), not to even
    x = paid(book, "tranche_2", t) * 100
    assert (x.numerator * 2 + x.denominator) // (2 * x.denominator) / 100 == A["q3_tranche_2_debt_service_2020_12_31"]


def test_q4_the_share_of_tranche_4s_interest_that_is_capitalised(book):
    total = sum(mv(book, "tranche_4.interest", t) for t in range(N))
    capitalised = book.balance("tranche_4.capitalised_interest", q("2016-09-30"))
    assert capitalised == 22_000_000 * ((1 + F(82, 4000)) ** 8 - 1)
    assert round(float(capitalised / total) * 100, 2) == A["q4_tranche_4_capitalised_share_pct"]


def test_q5_the_facilitys_interest_in_2016(book):
    total = sum(mv(book, f"{loan}.interest", t) for loan in TRANCHES for t in range(N) if date(t)[:4] == "2016")
    assert round(float(total), 2) == A["q5_facility_interest_cy2016"]


def test_q6_the_first_quarter_end_the_facility_is_below_40m_after_being_above(book):
    total = [sum(owed(book, loan, t) for loan in TRANCHES) for t in range(N)]
    first = next(t for t in range(1, N) if total[t] < 40_000_000 <= max(total[:t]))
    assert date(first) == A["q6_first_quarter_below_40m"]


# ── how each tranche is repaid ────────────────────────────────────────────

def test_tranche_1_pays_the_textbook_annuity(book):
    # on 30/360 every quarter has the same rate, so the annuity loop rule's payment is PMT
    r = F(825, 40000)
    pmt = 14_000_000 * r / (1 - (1 + r) ** -28)
    pays = [paid(book, "tranche_1", t) for t in range(1, 29)]
    assert max(abs(float(p - pmt)) for p in pays) < 1e-6


def test_tranche_4_repays_16_equal_steps_of_its_capitalised_balance(book):
    start = owed(book, "tranche_4", q("2016-09-30"))
    assert start == 22_000_000 * (1 + F(82, 4000)) ** 8
    steps = [owed(book, "tranche_4", t - 1) - owed(book, "tranche_4", t) for t in range(q("2016-12-31"), q("2020-09-30") + 1)]
    assert len(steps) == 16 and max(abs(float(s - start / 16)) for s in steps) < 1e-6


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_capitalised_interest_is_repaid_before_the_principal(book):
    # the first steps go to the 4.03M of capitalised interest; the 22M is touched in the third
    first = q("2016-12-31")
    assert [book.balance("tranche_4.debt", t) for t in (first, first + 1)] == [22_000_000, 22_000_000]
    assert book.balance("tranche_4.capitalised_interest", first + 2) == 0 and book.balance("tranche_4.debt", first + 2) < 22_000_000


def test_finding_equal_steps_of_the_commitment_are_not_the_papers_steps():
    # the loan's own equal steps repay the capitalised interest at once with the first step
    def own_steps(case):
        del case["loops"]["tranche_4_steps"]
        case["contracts"]["tranche_4"]["terms"]["principal_schedule"] = None
    b = variant(own_steps).group.book("company")
    first = q("2016-12-31")
    assert owed(b, "tranche_4", first - 1) - owed(b, "tranche_4", first) == \
        22_000_000 * ((1 + F(82, 4000)) ** 8 - 1) + F(22_000_000, 16)
    total = sum(mv(b, "tranche_4.interest", t) for t in range(N))
    share = b.balance("tranche_4.capitalised_interest", first - 1) / total
    assert round(float(share) * 100, 2) != A["q4_tranche_4_capitalised_share_pct"]


def test_finding_the_facility_starts_below_40m(book):
    # only tranches 1 and 2 are drawn in the first two quarters: Q6 asks for the first fall below 40M
    total = [sum(owed(book, loan, t) for loan in TRANCHES) for t in range(N)]
    assert total[0] == 39_000_000 and total[1] < 40_000_000 < total[2]
