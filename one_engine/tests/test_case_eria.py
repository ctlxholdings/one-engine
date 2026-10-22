"""Reproduction test: ERIA hospital shared-savings EPC (plan step 6).

Published worked example (ERIA, ESCO models, Cambodia, 2020), reproduced
blind by two engineers in esco-product (`engine/tests/test_m2.py`): the
provider invests 850,000 at t0; savings of 3,080,573 kWh a year at a tariff
of 0.108 are split 80/20 for 8 years. Published answers: provider income
266,161.51 a year, simple payback 3.1935 years, IRR 26.554%. Tolerances
are esco-product's, set at the published resolution.

If this fails, the engine or the configuration is wrong, never the number.
"""
import pathlib

import pytest

from one_engine.cases import run_case
from one_engine.inputs import State

CASE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "eria.yaml"


@pytest.fixture(scope="module")
def eria():
    return run_case(CASE)


def test_provider_income_each_year(eria):
    for t in range(1, 9):
        assert float(eria.cut("provider_project_cash_flow").values[t]) == pytest.approx(266161.51, abs=0.005)


def test_client_keeps_its_share(eria):
    for t in range(1, 9):
        assert float(eria.cut("client_cash_flow").values[t]) == pytest.approx(66540.3768, abs=0.005)


def test_payback(eria):
    assert eria.measure("payback") == pytest.approx(3.1935, abs=5e-5)


def test_irr(eria):
    assert eria.measure("irr") == pytest.approx(0.26554, abs=5e-6)


def test_books_pass_integrity(eria):
    assert eria.failed_checks() == []


def test_the_case_file_has_no_unread_key():
    import yaml
    from one_engine.inputs import InputError
    data = yaml.safe_load(CASE.read_text())
    data["promote"] = {"pref_rate": 0.08}            # planted: a block nobody reads
    with pytest.raises(InputError, match="promote"):
        run_case(data)


def test_a_stand_in_tariff_flags_the_irr():
    import yaml
    data = yaml.safe_load(CASE.read_text())
    data["contracts"]["savings"]["terms"]["tariff"] = {"value": 0.108, "state": "stand_in", "source": "proxy"}
    res = run_case(data)
    assert State.STAND_IN in res.measure_states("irr")
