"""The esco-product LOD 0 deals run through the oracle harness, and the pieces they rely on.

Each way an esco receipt could be earned dishonestly is planted here and must
be caught:

* a number typed into a translation instead of read from the deal's numbers
  fails the ``translation traced`` check;
* a figure read as a ``driver`` that only repeats an input (so it is compared
  with itself) is refused;
* a check the answer key marks ``xfail`` cannot be read: it is set aside by
  the oracle itself, so reading it is a failed check;
* a deal the answer key holds no figure for is ``blocked``.

Then every deal gets its recorded verdict: those with a translation match with
the planted error caught, except a deal whose record differs from its own
source, which differs at exactly the figures named in ``RECORD_FINDINGS``; the
rest are blocked.
"""
import pytest

from one_engine import oracles as O
from one_engine.oracles.esco import (ANSWER_KEY, CASES, TRANSLATIONS, EscoFamily, answer_key,
                                     checks_for, esco_inputs, _case)

FAMILY = EscoFamily()
ORACLES = FAMILY.oracles()
TRANSLATED = {p.stem for p in TRANSLATIONS.glob("*.yaml")}
#: the five sealed holdout deals (esco-product cases/sources/HOLDOUT_SEALED.md): never vendored
HOLDOUT_WORDS = ("washington", "bundesliegenschaften", "jva_nuernberg", "tinker", "weinbau")
#: translated deals whose receipt is ``differs`` because the case file's record disagrees with the
#: source's own arithmetic, with every figure that differs; the cause is in the translation's header
RECORD_FINDINGS = {
    # the record takes 80% of the source's ROUNDED annual savings; the books take 80% of the exact ones
    "eria_hospital": {"eria.esco_share_record"},
}
#: differing deals whose plant cannot be seen, with why
UNPLANTABLE: dict = {}


def test_every_deal_and_the_answer_key_are_vendored_and_in_the_manifest():
    assert len(ORACLES) == 75
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"{CASES}/{oid}-lod0.yaml" in files
        O.verify(f"{CASES}/{oid}-lod0.yaml")
    assert ANSWER_KEY in files
    O.verify(ANSWER_KEY)


def test_no_holdout_deal_is_vendored():
    assert not [oid for oid in ORACLES if any(w in oid for w in HOLDOUT_WORDS)]


def test_the_answer_key_is_read_whole():
    # 489 checks at the vendored hash: 333 REC, 132 SRC, 24 BAND, 11 of them xfail
    checks = answer_key()["CHECKS"]
    kinds = [c[1] for c in checks]
    assert len(checks) == 489
    assert (kinds.count("REC"), kinds.count("SRC"), kinds.count("BAND")) == (333, 132, 24)
    assert sum(1 for c in checks if c[7]) == 11


def test_inputs_leave_out_the_transcribers_arithmetic_and_read_text_for_its_number():
    inputs = esco_inputs(_case("eria_hospital"))
    assert inputs["chiller_savings_kwh_per_y"] > 0
    assert not [k for k in inputs if k.startswith(("derived", "engine_inputs"))]
    # a number printed in text is read the way the answer key reads it
    num = answer_key()["num"]
    assert num("220 000 (source; rounded)") == 220000
    # a deal whose numbers are all printed with their source citation still has its inputs
    assert esco_inputs(_case("birmingham_pfi_amey"))["term_years"] == 25


@pytest.mark.parametrize("oid", ORACLES)
def test_every_deal_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid in RECORD_FINDINGS:
        assert r.verdict == "differs", r.reason
        assert {c["name"] for c in r.checks if not c["ok"]} == RECORD_FINDINGS[oid]
        assert r.planted_caught or oid in UNPLANTABLE, "the plant broke none of the checks that agree"
    elif oid in TRANSLATED:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks >= 3
    else:
        assert r.verdict == "blocked", (r.verdict, r.reason)


def _planted_copy(tmp_path, oid, old, new):
    """A copy of one translation with ``old`` replaced by ``new``."""
    folder = tmp_path / "esco"
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text()
    assert text.count(old) == 1, old
    (folder / f"{oid}.yaml").write_text(text.replace(old, new))
    return EscoFamily(folder)


def test_a_number_typed_into_a_translation_fails_the_gate(tmp_path):
    # the tariff typed in; it is the deal's own number, so only the gate can see it
    tariff = esco_inputs(_case("eria_hospital"))["tariff_usd_per_kwh"]
    fam = _planted_copy(tmp_path, "eria_hospital",
                        "tariff: {generator: src, output: tariff_usd_per_kwh}", f"tariff: {float(tariff)}")
    r = O.receipt(fam, "eria_hospital", commit="test")
    gate = [c for c in r.checks if c["name"] == "translation traced"]
    assert r.verdict == "differs" and not gate[0]["ok"] and "0.108" in gate[0]["got"]


def test_a_driver_that_repeats_an_input_is_refused(tmp_path):
    # the year's kWh read straight from one input: the figure would be compared with itself
    fam = _planted_copy(tmp_path, "eria_hospital", "eria.total_kwh: {driver: src.kwh, at: 1}",
                        "eria.total_kwh: {driver: src.chiller_savings_kwh_per_y, at: 1}")
    r = O.receipt(fam, "eria_hospital", commit="test")
    assert r.verdict == "differs" and "read straight from an input" in r.reason and "eria.total_kwh" in r.reason


def test_a_check_the_answer_key_marks_xfail_cannot_be_read(tmp_path):
    xfail = [c[0] for c in checks_for("eria_hospital") if c[7]]
    assert xfail, "eria_hospital carries the source's own 266,161.50 as an xfail check"
    fam = _planted_copy(tmp_path, "eria_hospital", "figures:\n",
                        f"figures:\n  {xfail[0]}: {{cut: esco_revenue, at: 1}}\n")
    r = O.receipt(fam, "eria_hospital", commit="test")
    assert r.verdict == "differs" and xfail[0] in r.reason


def test_a_deal_with_no_figure_in_the_answer_key_is_blocked(tmp_path):
    bare = [oid for oid in ORACLES if not checks_for(oid)]
    assert len(bare) == 7
    # even with a translation, nothing it reads can be compared
    folder = tmp_path / "esco"
    folder.mkdir()
    (folder / f"{bare[0]}.yaml").write_text(f"esco: {bare[0]}\nentity: x\nplant: x\ncase: {{}}\nfigures: {{}}\n")
    r = O.receipt(EscoFamily(folder), bare[0], commit="test")
    assert r.verdict == "blocked" and "no figure" in r.reason
