"""The golds library's external fixtures run through the oracle harness.

Each way an external receipt could be earned dishonestly is planted here and
must be caught:

* a number typed into a translation instead of read from the fixture fails the
  ``translation traced`` check;
* an answer handed in as an input: the fixture's answers never reach the
  formulas generator (the library's own test feeds several back in);
* a figure set aside: every figure is a published answer, so setting one
  aside is refused;
* a tolerance wider than the library's own: each figure is held to the
  library test's CENT, except the four the module docstring names.

Then every fixture gets its recorded verdict: it matches with the planted
error caught, or differs at exactly the figures named in ``UNREAD`` (figures
the inputs cannot derive, or that need an engine piece that does not exist;
each translation's header says which and why), with the plant still caught
on the figures that agree.
"""
from fractions import Fraction

import pytest

from one_engine import oracles as O
from one_engine.oracles.external import (FOLDER, TESTS, TRANSLATIONS, ExternalFamily, _fixture, cent,
                                         external_figures, external_inputs)

FAMILY = ExternalFamily()
ORACLES = FAMILY.oracles()
OC_IC = {f"{k}.test_{i}" for k in ("oc_ratios", "oc_status", "ic_ratios", "ic_status") for i in range(4)}
#: figures each fixture leaves unread, with why (each translation's header says it in full)
UNREAD = {
    # the extract carries no working-capital driver (days or shares of revenue and COGS)
    "cfi_3statement": {"cash", "ar", "inventory", "ap"},
    # the extract carries no capital gains tax rate or cost base
    "modeloff_property": {"cgt", "exit_cash"},
    # the waterfall's tests are on debt service; it has no over-collateralisation or interest-coverage test
    "open_securitisation": OC_IC,
}


def test_the_five_fixtures_and_the_librarys_test_are_vendored_and_in_the_manifest():
    assert ORACLES == ["cfi_3statement", "ias29_hyperinflation", "ias36_impairment", "modeloff_property",
                       "open_securitisation"]
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"{FOLDER}/{oid}.json" in files
        O.verify(f"{FOLDER}/{oid}.json")
    O.verify(TESTS)
    # the golds engine's own pinned output is not an outside answer, and is not vendored
    assert f"{FOLDER}/engine_pinned.json" not in files


def test_the_comparison_rule_is_the_library_tests_own():
    assert cent() == Fraction(1, 100)
    _, tol = FAMILY._load("ias36_impairment")
    # the pro rata lines at the fixture's tolerance (printed to the thousand); goodwill, total and loss at CENT
    assert tol["figures"] == {"property": 500, "plant": 500, "patent": 500} and tol["default"] == cent()
    assert FAMILY._within("property", Fraction(2215000), Fraction("2215384.6154"), tol, None)[0]
    assert not FAMILY._within("cgu_total", Fraction(4800000), Fraction("4800000.02"), tol, None)[0]
    _, tol = FAMILY._load("open_securitisation")
    assert tol["default"] == Fraction("1e-9") and not tol["figures"]
    assert FAMILY._within("bond_notionals.A1", Fraction(3, 4), Fraction("0.7500000005"), tol, None)[0]
    assert not FAMILY._within("bond_notionals.A1", Fraction(3, 4), Fraction("0.750000002"), tol, None)[0]


def test_no_answer_is_an_input():
    same = {}
    for oid in ORACLES:
        fixture = _fixture(oid)
        figures = external_figures(fixture)
        assert figures, oid
        inputs = external_inputs(fixture)
        answers = {tuple(v) for v in figures.values() if isinstance(v, list)}
        same[oid] = [k for k, v in inputs.items() if isinstance(v, list) and tuple(v) in answers]
    # one input series equals an answer series by coincidence: the pool's notional (1 until it is
    # repaid, then 0) reads like the coverage tests' pass flags (1 while they run, then 0)
    assert same == {oid: (["notional"] if oid == "open_securitisation" else []) for oid in ORACLES}
    cfi = external_inputs(_fixture("cfi_3statement"))
    assert "revenue" in cfi and not {"cash", "ar", "inventory", "ap", "equity"} & set(cfi)
    sec = external_inputs(_fixture("open_securitisation"))
    assert "interest_proceeds" in sec and not [k for k in sec if "payments" in k]


@pytest.mark.parametrize("oid", ORACLES)
def test_every_fixture_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    if oid in UNREAD:
        assert r.verdict == "differs", r.reason
        failed = [c for c in r.checks if not c["ok"]]
        assert {c["name"] for c in failed} == UNREAD[oid]
        assert all(c["got"] == "not reported" for c in failed)
    else:
        assert r.verdict == "matches", r.reason
    assert r.planted_caught, "the plant broke none of the checks that agree"


def _planted_copy(tmp_path, oid, old, new):
    """A copy of one translation with ``old`` replaced by ``new``."""
    folder = tmp_path / "external"
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text()
    assert text.count(old) == 1, old
    (folder / f"{oid}.yaml").write_text(text.replace(old, new))
    return ExternalFamily(folder)


def test_a_number_typed_into_a_translation_fails_the_gate(tmp_path):
    fam = _planted_copy(tmp_path, "ias29_hyperinflation", "rise: index_close / index_open - 1", "rise: 0.4")
    r = O.receipt(fam, "ias29_hyperinflation", commit="test")
    gate = [c for c in r.checks if c["name"] == "translation traced"]
    assert r.verdict == "differs" and not gate[0]["ok"] and "0.4" in gate[0]["got"]


def test_no_figure_can_be_set_aside(tmp_path):
    fam = _planted_copy(tmp_path, "cfi_3statement", "figures:\n",
                        "not_compared:\n  cash: the extract has no working-capital driver\nfigures:\n")
    r = O.receipt(fam, "cfi_3statement", commit="test")
    assert r.verdict == "differs" and "scored figures set aside ['cash']" in r.reason
