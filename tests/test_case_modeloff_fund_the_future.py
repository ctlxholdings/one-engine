"""ModelOff 2016 "Fund the Future": four savings accounts over 23 to 44 years (plan step 7o).

Referee: the competition's ten answers (Q6-Q15), with the savers' terms,
as transcribed from its Inputs sheet in
``generic_engine/tests/test_benchmark_round3_fund_the_future.py`` and copied
to ``cases/reference/modeloff_2016_fund_the_future.json`` by
``consolidation/experiments/make_reference_modeloff_fund_the_future.py``
(each term and answer checked against that module's text). The workbook is
not in the repository, so the transcription is not re-checked against it
here. The answers are stated to the dollar or as years and names; one_engine
must round to exactly them. Q11 (a salary) is a check of the inputs layer
only: salaries are grown when the case file is written, and this test grows
them again from the reference.

What the case adds to the engine (convergence count 0): no shared module
changed. Pack change (not core): a ``deposit_account`` contract earns a
return on its opening balance (compounded into it), takes scheduled
deposits and is withdrawn in full when it closes. Reused unchanged: a rate
and a days series by period (an annual return on a quarterly grid is 360
days in the year's last quarter and none in the others), ``pay_as_due``
(step 7l) in two phases, one entity per saver, and the cash flow cut.
"""
import copy
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "modeloff_fund_the_future.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "modeloff_2016_fund_the_future.json").read_text())
I, A = REF["inputs"], REF["answers"]
N = CASE["periods"]
SAVERS = ("alice", "bob", "charlie", "diana")


def end(y):
    """The period of 31 December of year ``y``: period 0 is the quarter ending 31 December 2016."""
    return (y - 2016) * 4


def year(t):
    return 2016 + (t + 3) // 4


def salary(s, y):
    """The paper's salary: the first year's, grown by each later year's rule (a rate, or Charlie's +5,500)."""
    x = F(I[s]["salary"])
    for k in range(2018, y + 1):
        if s == "charlie":
            x += 5500
        elif s == "alice":
            x *= F(103, 100)
        elif s == "bob":
            x *= F(1022, 1000) if k <= 2026 else F(1028, 1000)
        else:
            x *= 1 if k <= 2025 else F(105, 100) if k <= 2040 else F(1015, 1000)
    return x


def deposits(s, closes):
    """The paper's deposits by period, from the reference's words."""
    out = [F(0)] * N
    out[0] = F(I[s]["initial"])
    share = F(I[s]["core"]["share"])
    for t in range(1, end(closes) + 1):
        y, year_end = year(t), t % 4 == 0
        if s == "bob":
            out[t] += share * salary(s, y) + (20_000 if t == end(2028) - 2 else 0)        # 30 June 2028
        elif year_end:
            out[t] += share * salary(s, y)
        if s == "charlie":
            out[t] += 1000
        if s == "diana" and year_end and y >= 2021 and (y - 2021) % 5 == 0:
            out[t] += 5000
    return out


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


def paid(res, ref):
    """What was paid on a claim, by period."""
    return {s.period: s.paid for s in res.group.settlements if s.ref == ref}


def final(res, s):
    """The balance withdrawn when the account closes."""
    (t, x), = paid(res, f"{s}_account.withdraw").items()
    return x


def returns(res, s, periods):
    b = res.group.book(s)
    return sum(b.balance(f"{s}_account.return", t) - b.opening(f"{s}_account.return", t) for t in periods)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_deposits_and_the_returns():
    for s in SAVERS:
        terms = CASE["contracts"][f"{s}_account"]["terms"]
        assert terms["close_at"] == end(I[s]["closes"]) and terms["day_count_basis"] == 360
        assert [F(x) for x in terms["schedule"]] == deposits(s, I[s]["closes"])
        rate = [F(x) * F(d, 360) for x, d in zip(terms["rate"], terms["days_per_period"])]
        if s in ("bob", "charlie"):                    # each quarter, at the stated quarterly rate
            assert rate[1:] == [F(I[s]["returns"][0]["rate"])] * (N - 1)
        else:                                          # at year end, at the year's rate
            for t in range(1, N):
                r = F("0.04") if s == "alice" else F("0.08") if year(t) <= 2038 else F("0.05")
                assert rate[t] == (r if t % 4 == 0 else 0)
        assert rate[0] == 0


# ── the answers ───────────────────────────────────────────────────────────

def test_the_case_runs_clean_and_every_account_closes_empty(res):
    assert res.failed_checks() == []
    for s in SAVERS:
        b = res.group.book(s)
        assert b.balance(f"{s}_account", end(I[s]["closes"])) == 0
        assert b.balance(f"{s}_account", end(I[s]["closes"]) - 1) > 0


def test_q6_alices_balance_at_the_end_of_2024(res):
    assert round(res.group.book("alice").balance("alice_account", end(2024))) == A["q6_alice_balance_2024"]


def test_q7_alices_core_deposits(res):
    core = sum(x for t, x in paid(res, "alice_account.deposit").items() if t > 0)
    assert core == sum(F(9, 100) * salary("alice", y) for y in range(2017, 2045))
    assert round(core) == A["q7_alice_core_deposits"]


def test_q8_bobs_returns_2019_to_2023(res):
    x = returns(res, "bob", range(end(2018) + 1, end(2023) + 1))
    assert round(x) == A["q8_bob_returns_2019_2023"]


def test_q9_the_first_year_end_bobs_balance_is_above_200k(res):
    b = res.group.book("bob")
    assert next(y for y in range(2017, 2058) if b.balance("bob_account", end(y)) > 200_000) == \
        A["q9_bob_first_year_above_200k"]


def test_q10_charlies_final_balance(res):
    assert round(final(res, "charlie")) == A["q10_charlie_final_balance"]


def test_q11_dianas_salary_in_2050(res):
    # the 2050 deposit is 4% of the salary (no additional deposit that year)
    deposit = paid(res, "diana_account.deposit")[end(2050)]
    assert deposit == F(4, 100) * salary("diana", 2050)
    assert round(deposit / F(4, 100)) == A["q11_diana_salary_2050"]


def test_q12_dianas_returns_over_the_accounts_life(res):
    assert round(returns(res, "diana", range(N))) == A["q12_diana_total_returns"]


def test_q13_the_two_largest_final_balances(res):
    ranked = sorted(SAVERS, key=lambda s: final(res, s), reverse=True)
    assert ranked[:2] == A["q13_two_largest_final_balances"]


def test_q14_the_four_balances_at_the_end_of_2039(res):
    # Charlie's account closes that day: its balance is what is withdrawn
    open_ = sum(res.group.book(s).balance(f"{s}_account", end(2039)) for s in ("alice", "bob", "diana"))
    assert round(open_ + final(res, "charlie")) == A["q14_sum_of_balances_2039"]


def test_q15_if_charlies_account_stays_open(res):
    def stays_open(case):
        terms = case["contracts"]["charlie_account"]["terms"]
        terms["close_at"] = N - 1
        terms["schedule"] = deposits("charlie", year(N - 1))
    b = variant(stays_open).group.book("charlie")
    first = next(y for y in range(2017, year(N - 1) + 1) if b.balance("charlie_account", end(y)) > 1_200_000)
    assert first == A["q15_charlie_open_first_year_above_1200k"]
    # the same account to 2039: staying open changes nothing before the close
    assert b.balance("charlie_account", end(2039) - 1) == res.group.book("charlie").balance("charlie_account", end(2039) - 1)


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_deposits_at_the_end_of_a_period_earn_nothing_in_it(res):
    # Alice's first return (2017) is 4% of the 12,820 she opened with, not of her 2017 deposit too
    assert returns(res, "alice", range(1, end(2017) + 1)) == F(4, 100) * 12_820


def test_finding_bobs_balance_crosses_200k_in_a_quarter_of_2037(res):
    # the paper reads year ends; the first quarter above 200,000 falls in the same year
    b = res.group.book("bob")
    first = next(t for t in range(N) if b.balance("bob_account", t) > 200_000)
    assert year(first) == A["q9_bob_first_year_above_200k"]
