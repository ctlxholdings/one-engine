"""Conditions (card 8): one level per test, a declared rule for missing facts."""
import pytest

from one_engine.conditions import ConditionError, Test, evaluate


def test_pass_and_fail_with_values_kept():
    t = Test("dscr_lockup", ">=", 1.1, if_missing="pass")
    r = evaluate(t, 1.25)
    assert r.verdict == "pass" and r.value == 1.25 and r.level == 1.1
    assert evaluate(t, 1.05).verdict == "fail"
    assert evaluate(t, 1.1).verdict == "pass"                     # the level itself passes ">="


def test_missing_fact_follows_the_declared_rule():
    assert evaluate(Test("x", ">=", 1, if_missing="pass"), None).verdict == "pass"
    assert evaluate(Test("x", ">=", 1, if_missing="fail"), None).verdict == "fail"
    assert evaluate(Test("x", ">=", 1, if_missing="unknown"), None).verdict == "unknown"
    with pytest.raises(ConditionError):
        evaluate(Test("x", ">=", 1), None)                         # no rule declared: refuse


def test_refusals():
    with pytest.raises(ConditionError):
        Test("x", "=>", 1)
    with pytest.raises(ConditionError):
        Test("x", ">=", 1, if_missing="maybe")
