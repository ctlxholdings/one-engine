"""The golds library's pilot golds run through the oracle harness.

A pilot gold has a promoted gold's layout, so the dishonest routes the hand
golds' tests plant (a typed number, an unmapped row, a gold without a
translation) are the same code and are planted there
(``test_oracles_golds.py``). Planted here: what the pilots added to that
code, a calendar label written as text, and a headline read through a gold
row of another name.

Then every pilot gold gets its recorded verdict: a translated one matches with
the planted error caught, except a pilot that contradicts itself, or whose row
needs an engine piece that does not exist, which differs at exactly the rows
named in ``PILOT_FINDINGS`` (the cause is in each translation's header); the
rest are blocked. (The income_tax rulebook's ``minimum`` was built for nine of
them: before it, cashew_taxholiday_v26 and pharmacy_expiry each had rows no book
held.)
"""
import pytest

from one_engine import oracles as O
from one_engine.oracles.pilot import FOLDER, TRANSLATIONS, PilotFamily

FAMILY = PilotFamily()
ORACLES = FAMILY.oracles()
TRANSLATED = {p.stem for p in TRANSLATIONS.glob("*.yaml")}
#: translated pilots whose receipt is ``differs``, with every row that differs; the oracle is never edited
PILOT_FINDINGS = {
    # the "if deductible" column ignores the minimum tax the gold's own section 8 imposes (3,000,000 binds)
    "juice_nondeductible": {"tax_if_deductible", "tax_leakage", "fcff_if_deductible"},
    # inventory_packaging uses the unrounded 33,333.33 bottles; the gold's own check and cost rows round
    "juice_nondeductible_v26": {"inventory_packaging", "nwc", "nwc_prior"},
}


def test_every_pilot_gold_is_vendored_and_in_the_manifest():
    assert len(ORACLES) == 23
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"{FOLDER}/{oid}.yaml" in files
        O.verify(f"{FOLDER}/{oid}.yaml")


def test_the_inventory_rows_of_the_pilot_golds_are_claimed():
    # a pilot is named by its path in the golds library's staging area, ``pilot/<id>.yaml``
    row = {"system": "golds (Mac: one-place)", "encoding_id": "g_hand_gold", "path": f"pilot/{ORACLES[0]}.yaml"}
    assert FAMILY.inventory_oid(row) == ORACLES[0]
    assert FAMILY.inventory_oid({**row, "path": f"cases/{ORACLES[0]}.yaml"}) is None
    assert FAMILY.inventory_oid({**row, "path": "pilot/not_a_gold.yaml"}) is None


@pytest.mark.parametrize("oid", ORACLES)
def test_every_pilot_gold_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid in PILOT_FINDINGS:
        assert r.verdict == "differs", r.reason
        assert {c["name"] for c in r.checks if not c["ok"]} == PILOT_FINDINGS[oid]
        assert r.planted_caught, "the plant broke none of the checks that agree"
    elif oid in TRANSLATED:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks > 3
    else:
        assert r.verdict == "blocked" and "no translation yet" in r.reason, (r.verdict, r.reason)


def _planted_copy(tmp_path, oid, old, new):
    """A copy of one pilot translation with ``old`` replaced by ``new``."""
    folder = tmp_path / FOLDER
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text()
    assert text.count(old) == 1, old
    (folder / f"{oid}.yaml").write_text(text.replace(old, new))
    return PilotFamily(folder)


def test_a_label_row_of_text_is_listed_not_compared_and_cannot_also_be_read(tmp_path):
    # grain_v26 names its months as printed ("Nov-2026"): listed under labels, the gold matches
    r = O.receipt(FAMILY, "grain_v26", commit="test")
    assert r.verdict == "matches" and "Nov-2026" not in str(r.checks)
    # a label that is also read is refused: a row is either a label or a quantity
    fam = _planted_copy(tmp_path, "grain_v26", "  fcff: {cut: fcff}\n", "  fcff: {cut: fcff}\n  period_label: {cut: fcff}\n")
    with pytest.raises(ValueError, match="label 'period_label'"):
        fam.run("grain_v26")


def test_the_headline_is_compared_when_the_gold_names_its_fcff_row_otherwise(tmp_path):
    # grain_v26's trace calls its fcff row fcff_xof; the headline is still read, by the mapping `fcff`
    fam = _planted_copy(tmp_path, "grain_v26", "  fcff: {cut: fcff}\n", "  fcff: {cut: fcff, sign: -1}\n")
    r = O.receipt(fam, "grain_v26", commit="test")
    assert r.verdict == "differs" and {c["name"] for c in r.checks if not c["ok"]} == {"fcff (headline)"}
