"""Every test belongs to exactly one of three groups, so a run can report them apart.

Ali (2026-10-13): many tests are not financial tests, and counting them together
"distorts the things I care about (financial correctness/accuracy/representativity)".
The groups are defined in :mod:`one_engine.tests.groups`; these tests hold the
classification honest: every file is placed, a new file must be placed by hand,
and a receipt is never confused with a test of the harness that writes it.
"""
import pytest

from one_engine.tests.groups import GROUPS, TESTS, group, tally


def test_every_test_file_is_placed_in_a_group():
    for f in TESTS.glob("test_*.py"):
        assert group(f.name, "test_anything") in GROUPS, f.name


def test_a_receipt_is_an_answer_and_the_harness_around_it_is_code():
    assert group("test_oracles_published.py", "test_every_published_case_gets_its_recorded_verdict") == "answers"
    assert group("test_oracles.py", "test_every_actus_oracle_gets_its_recorded_verdict") == "answers"
    assert group("test_oracles.py", "test_each_verdict_is_given_only_when_earned") == "code"
    assert group("test_oracles_golds.py", "test_the_lower_tax_bands_are_relieved") == "code"


def test_cases_and_financial_rules_are_finance_and_guards_are_code():
    assert group("test_case_nightmare.py", "test_anything") == "finance"
    assert group("test_tax.py", "test_anything") == "finance"
    assert group("test_mutations.py", "test_anything") == "code"
    assert group("test_inputs.py", "test_anything") == "code"


def test_a_new_file_must_be_placed_by_hand():
    with pytest.raises(KeyError, match="test_new_thing.py"):
        group("test_new_thing.py", "test_anything")


def test_the_tally_counts_each_outcome_per_group():
    got = tally([("answers", "passed"), ("answers", "failed"), ("code", "passed"), ("answers", "passed")])
    assert got == {"answers": {"passed": 2, "failed": 1}, "finance": {}, "code": {"passed": 1}}
