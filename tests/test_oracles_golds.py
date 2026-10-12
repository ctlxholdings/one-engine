"""The golds library run through the oracle harness, and the pieces it relies on.

Each way a gold receipt could be earned dishonestly is planted here and must
be caught:

* a number typed into a translation instead of read from the gold fails
  the ``translation traced`` check;
* a gold trace row the translation does not map is a failed check, not a
  skip;
* a gold without a translation is ``blocked``, never ``matches``.

Then every hand gold gets its recorded verdict: those with a translation
match with the planted error caught; the rest are blocked.
"""
import shutil
from fractions import Fraction

import pytest

from one_engine import oracles as O
from one_engine.inputs import InputError, Reader
from one_engine.oracles.golds import GoldFamily, TRANSLATIONS, untraced
from one_engine.packs.tea import formulas

FAMILY = GoldFamily()
ORACLES = FAMILY.oracles()
#: golds whose translation reproduces them; every other hand gold is blocked until translated
TRANSLATED = {"bakery", "moto", "bakery_growth", "bakery_priceindex", "bakery_oven", "bakery_wc"}


def test_every_hand_gold_is_vendored_and_in_the_manifest():
    assert len(ORACLES) == 32
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"golds/{oid}.yaml" in files
        O.verify(f"golds/{oid}.yaml")


@pytest.mark.parametrize("oid", ORACLES)
def test_every_hand_gold_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid in TRANSLATED:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks > 3
    else:
        assert r.verdict == "blocked" and "no translation yet" in r.reason, (r.verdict, r.reason)


def _planted_copy(tmp_path, oid, old, new):
    folder = tmp_path / "golds"
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text()
    assert text.count(old) == 1, old
    (folder / f"{oid}.yaml").write_text(text.replace(old, new))
    return GoldFamily(folder)


def test_a_number_typed_into_a_translation_fails_the_gate(tmp_path):
    # the price is typed in; it happens to be the gold's value, so only the gate can see it
    fam = _planted_copy(tmp_path, "bakery", "price: {generator: gold, output: price_per_loaf}", "price: 250")
    r = O.receipt(fam, "bakery", commit="test")
    assert r.verdict == "differs"
    assert r.first_difference["name"] == "translation traced" and "250" in r.first_difference["got"]


def test_an_unmapped_trace_row_is_a_failed_check(tmp_path):
    fam = _planted_copy(tmp_path, "bakery", "  rent: {cut: rent, sign: -1}\n", "")
    r = O.receipt(fam, "bakery", commit="test")
    assert r.verdict == "differs"
    assert {"name": "rent", "got": "not reported"}.items() <= r.first_difference.items()


def test_a_wrong_mapping_is_seen(tmp_path):
    # labour read where flour is meant: both are costs, the numbers differ
    fam = _planted_copy(tmp_path, "bakery", "flour_cogs: {cut: flour, sign: -1}", "flour_cogs: {cut: labour, sign: -1}")
    r = O.receipt(fam, "bakery", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "flour_cogs"


def test_the_gate_reads_numbers_inside_formulas():
    assert untraced({"values": {"a": "x * 12", "b": "x * 365"}, "periods": 5}) == ["values.b ('x * 365') = 365"]
    assert untraced({"kind": "formulas", "type": "offtake", "rate": 0.3}) == ["rate = 0.3"]


# ── the formulas generator ──────────────────────────────────────────────────

def _gen(values, inputs, periods=3):
    return formulas(Reader({"periods": periods, "inputs": inputs, "values": values}, source="test"))


def test_formulas_are_exact_and_read_names():
    out = _gen({"tax": "ebit * rate", "half": "rate / 2"}, {"ebit": 100, "rate": 0.3})
    assert out["tax"] == 30 and out["half"] == Fraction(3, 20)        # 0.3 is 3/10, not the float


def test_formulas_make_series_when_they_read_the_period():
    out = _gen({"ramp": "base * (1 + t)", "after": "ramp * (t >= start)", "flat": {"each_period": "base"}},
               {"base": 10, "start": 1})
    assert out["ramp"] == [10, 20, 30] and out["after"] == [0, 20, 30] and out["flat"] == [10, 10, 10]


def test_formulas_refuse_an_unknown_name_and_a_reused_one():
    with pytest.raises(InputError, match="neither an input nor an earlier value"):
        _gen({"a": "b * 2"}, {"c": 1})
    with pytest.raises(InputError, match="already an input"):
        _gen({"c": "c * 2"}, {"c": 1})
