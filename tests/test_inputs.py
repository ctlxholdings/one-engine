"""Tests for one_engine.inputs: inputs are read once, and nothing is silent.

Planted case: the nightmare fixture writes `financial.promote`, the old
loader reads `financial.promote_terms`, so the promote is silently ignored
(consolidation ASSEMBLY_TEST_PAPER.md §11.4). A Reader refuses that.
"""
from decimal import Decimal

import pytest

from one_engine.inputs import InputError, Reader, State, load_yaml_strict


def test_unknown_key_refuses_at_finish():
    r = Reader({"promote": {"pref_rate": 0.08}, "tax_rate": 0.25}, source="nightmare.yaml")
    assert r.take("tax_rate") == 0.25
    with pytest.raises(InputError, match="promote"):
        r.finish()


def test_missing_required_key_refuses():
    r = Reader({}, source="x")
    with pytest.raises(InputError, match="tax_rate"):
        r.take("tax_rate")


def test_a_default_is_tagged_as_a_default():
    r = Reader({}, source="x")
    v = r.take_or_default("dscr_default", 1.05, reason="term sheet silent")
    assert v.value == 1.05 and v.state is State.DEFAULT and "term sheet silent" in v.source


def test_a_stated_value_carries_its_source():
    r = Reader({"tax_rate": 0.25}, source="params.yaml")
    v = r.take_value("tax_rate")
    assert v.state is State.STATED and v.source == "params.yaml:tax_rate"
    r.finish()


def test_a_key_written_twice_in_the_source_refuses():
    """YAML loaders keep the last of two duplicate keys and say nothing;
    one source per number means the second entry is an error."""
    from one_engine.inputs import load_yaml_strict
    with pytest.raises(InputError, match="tax_rate"):
        load_yaml_strict("tax_rate: 0.25\ntax_rate: 0.30\n")
    assert load_yaml_strict("tax_rate: 0.25\n") == {"tax_rate": 0.25}


def test_a_decimal_in_a_file_stays_the_decimal_it_names():
    from decimal import Decimal
    from one_engine.inputs import load_yaml_strict
    assert load_yaml_strict("tariff: 0.108\n") == {"tariff": Decimal("0.108")}


def test_a_merge_keeps_one_source_and_explicit_keys_override_it():
    data = load_yaml_strict("base: &b {rate: 0.1, annual: 5}\nother: {<<: *b, annual: 7}\n")
    assert data["other"] == {"rate": Decimal("0.1"), "annual": 7}
    with pytest.raises(InputError):
        load_yaml_strict("other: {annual: 5, annual: 7}\n")


# ── classes on inputs (credit pilot C1, 2026-10-15) ─────────────────────────

def test_an_input_scheme_names_the_class_of_each_input():
    from one_engine.inputs import Scheme
    credit = Scheme("credit", {"achievement_pct": "operating_performance", "specific_yield": "revenue_quantity"})
    assert credit.classify(["specific_yield", "achievement_pct"]) == {
        "specific_yield": "revenue_quantity", "achievement_pct": "operating_performance"}


def test_an_input_the_scheme_does_not_classify_refuses():
    # a shocked input with no class would drop out of the credit reading silently
    from one_engine.inputs import Scheme
    credit = Scheme("credit", {"achievement_pct": "operating_performance"})
    with pytest.raises(InputError, match="credit.*does not classify: rate"):
        credit.classify(["achievement_pct", "rate"])


def test_accounts_and_inputs_are_classified_by_one_scheme_type():
    from one_engine import cuts, inputs
    assert cuts.Scheme is inputs.Scheme
