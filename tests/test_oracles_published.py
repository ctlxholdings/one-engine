"""The published cases run through the oracle harness, and the pieces they rely on.

Each way a published-case receipt could be earned dishonestly is planted
here and must be caught:

* a number typed into a translation instead of read from the case's inputs
  fails the ``translation traced`` check;
* a published figure the translation neither reads nor sets aside with a
  reason is a failed check, not a skip;
* a figure the library itself scores (its ``mapping``) cannot be set aside;
* a figure read with the wrong sign is seen;
* a case without a tolerance and a reason does not run, as the library rules;
* a case without a translation is ``blocked``, never ``matches``.

Then every published case gets its recorded verdict: those with a
translation match with the planted error caught, except a case the source
contradicts, which differs at the figure its translation's notes explain;
the rest are blocked.
"""
from fractions import Fraction

import pytest

from one_engine import oracles as O
from one_engine.oracles.published import (PublishedFamily, TRANSLATIONS, _printed, figures,
                                          published_inputs)

FAMILY = PublishedFamily()
ORACLES = FAMILY.oracles()
TRANSLATED = {p.stem for p in TRANSLATIONS.glob("*.yaml")}
#: translated cases whose receipt is ``differs`` because the source contradicts itself, with the
#: figure it is first seen at; the cause is in the translation's notes, and the oracle is never edited
SOURCE_FINDINGS: dict = {}


def test_every_published_case_is_vendored_and_in_the_manifest():
    assert len(ORACLES) == 103
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"published/{oid}.yaml" in files
        O.verify(f"published/{oid}.yaml")


@pytest.mark.parametrize("oid", ORACLES)
def test_every_published_case_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid in SOURCE_FINDINGS:
        assert r.verdict == "differs" and r.first_difference["name"] == SOURCE_FINDINGS[oid], r.reason
    elif oid in TRANSLATED:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks >= 3
    else:
        assert r.verdict == "blocked", (r.verdict, r.reason)


def test_inputs_are_named_by_the_shortest_tail_no_other_input_shares():
    case = {"inputs": {"revenue": 900, "costs": {"rent": 10, "fuel": {"value": 5, "unit": "XAF"}},
                       "movements": {"rent": 10}, "note": "text is not an input",
                       "journal": [{"amount": 7, "debit": "cash"}, {"amount": 8}],
                       "by_year": {2016: 1.5}, "series": [1, 2, 3]}}
    got = published_inputs(case)
    assert got == {"revenue": 900, "costs__rent": 10, "fuel": 5, "movements__rent": 10,
                   "journal_0__amount": 7, "journal_1__amount": 8, "_2016": Fraction(3, 2),
                   "series": [1, 2, 3]}


def test_figures_are_every_number_printed_including_a_number_printed_as_text():
    case = {"expected": {"profit": {"value": 850, "prior_year": 700, "composition": "900 - 50 = 850"},
                         "answer": "$2,394.98", "share": "13%", "flag": True, "years": [1, 2],
                         "table": {"cash": {"debit": 35}}}}
    assert figures(case) == {"profit": 850, "profit.prior_year": 700, "answer": Fraction(239498, 100),
                             "share": 13, "years": [1, 2], "table.cash.debit": 35}
    assert _printed("-$1,500") == -1500 and _printed("about 1,500") is None


def test_the_tolerance_is_the_wider_of_abs_and_rel_and_quantities_have_their_own():
    tol = {"abs": 1, "rel": Fraction(2, 100), "quantities": {"quantity_sold": {"abs": 5}}}
    assert PublishedFamily._allowed(tol, None, Fraction(10)) == 1
    assert PublishedFamily._allowed(tol, None, Fraction(1000)) == 20
    assert PublishedFamily._allowed(tol, "quantity_sold", Fraction(1000)) == 5


def _planted_copy(tmp_path, oid, old, new, more=()):
    """A copy of one translation with ``old`` replaced by ``new`` (and each further pair in ``more``)."""
    folder = tmp_path / "published"
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text() if (TRANSLATIONS / f"{oid}.yaml").exists() else ""
    for a, b in ((old, new), *more):
        assert text.count(a) == 1, a
        text = text.replace(a, b)
    (folder / f"{oid}.yaml").write_text(text)
    return PublishedFamily(folder)


def test_a_number_typed_into_a_translation_fails_the_gate(tmp_path):
    # the year's sales typed in; it is the source's own figure, so only the gate can see it
    fam = _planted_copy(tmp_path, "atlas_broker_sawdust",
                        "price: {generator: src, output: vente_sciures}", "price: 900000")
    r = O.receipt(fam, "atlas_broker_sawdust", commit="test")
    assert r.verdict == "differs"
    assert r.first_difference["name"] == "translation traced" and "900000" in r.first_difference["got"]


def test_a_figure_neither_read_nor_set_aside_is_a_failed_check(tmp_path):
    line = "  adjusted_trial_balance_usd.cash.debit: {balance: cash, at: 1}\n"
    fam = _planted_copy(tmp_path, "fc_openstax_clipem_cliff_barbershop", line, "")
    r = O.receipt(fam, "fc_openstax_clipem_cliff_barbershop", commit="test")
    assert r.verdict == "differs"
    assert r.first_difference["name"] == "adjusted_trial_balance_usd.cash.debit"
    assert r.first_difference["got"] == "not reported"


def test_a_figure_the_library_scores_cannot_be_set_aside(tmp_path):
    line = "  net_income_top: {cut: net_income, total: true}\n"
    fam = _planted_copy(tmp_path, "fc_openstax_clipem_cliff_barbershop", line, "",
                        more=[("figures:\n", "not_compared:\n  net_income_top: not modelled\nfigures:\n")])
    r = O.receipt(fam, "fc_openstax_clipem_cliff_barbershop", commit="test")
    assert r.verdict == "differs" and "scored figures set aside ['net_income_top']" in r.reason


def test_a_figure_read_with_the_wrong_sign_is_seen(tmp_path):
    fam = _planted_copy(tmp_path, "fc_openstax_clipem_cliff_barbershop",
                        "unadjusted_trial_balance_usd.dividends.debit: {balance: dividends, sign: -1, at: 0}",
                        "unadjusted_trial_balance_usd.dividends.debit: {balance: dividends, at: 0}")
    r = O.receipt(fam, "fc_openstax_clipem_cliff_barbershop", commit="test")
    assert r.verdict == "differs"
    assert r.first_difference["name"] == "unadjusted_trial_balance_usd.dividends.debit"
    assert (r.first_difference["expected"], r.first_difference["got"]) == ("150", "-150")


def test_a_case_without_a_tolerance_does_not_run(tmp_path):
    # the ModelOff 2014 debt questions carry no tolerance: the library refuses them, and so does this family
    folder = tmp_path / "published"
    folder.mkdir()
    (folder / "modeloff_2014_precise_debt_modeling.yaml").write_text(
        "published: modeloff_2014_precise_debt_modeling\nentity: x\nplant: x\ncase: {}\nfigures: {}\n")
    r = O.receipt(PublishedFamily(folder), "modeloff_2014_precise_debt_modeling", commit="test")
    assert r.verdict == "blocked" and "no tolerance" in r.reason
