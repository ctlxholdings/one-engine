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
source, which differs at exactly the figures named in ``RECORD_FINDINGS``, and
a deal whose every comparable figure is printed too coarsely for a 1% plant to
show (``BLIND``: it agrees, but cannot count); the rest are blocked.
"""
from fractions import Fraction

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
    # the printed contract value is 238.52 months of the winning bid, not the 240 of the term
    "belo_horizonte_public_lighting_ppp": {"bh.value_over_investment"},
    # the page's 20.82m a year of budget relief contradicts its own "keine Haushaltsentlastung möglich"
    "berlin_pool24": {"pool24.relief_over_guarantee", "pool24.relief_over_base"},
    # the record multiplies the ROUNDED instalment (12 x 4,513); the books pay the exact annuity
    "geolumen_ener2crowd": {"geolumen.total_repaid"},
}
#: differing deals whose plant cannot be seen, with why
UNPLANTABLE: dict = {}
#: translated deals that agree but whose planted 1% change cannot show, because every figure they can
#: compare is printed coarser than 1% of it; each translation's header says which figures and why
BLIND = {
    "carpinteria_siemens_lease_purchase",   # only the lease term in whole years (principal left blank)
    "hungary_moe_school_espc_framework",    # one figure, a whole percent (45.045 printed 45)
    "kawarna_biomass",                      # 0.90, 40 and 15: per-unit and year counts, coarsely rounded
}


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
    # the transcriber's ``checks`` block is arithmetic over the inputs, like ``derived``: not an input
    assert not [k for k in esco_inputs(_case("baw_karlsruhe")) if k.startswith("checks")]


def test_a_printed_date_or_month_range_is_read_as_its_year_month_and_day():
    # as the answer key reads them (its date and phase_bounds): '2006-06-01', a {from, to} pair of
    # '04/2007' months, and a '01/2010 to 06/2016' range in one text
    # (names are the shortest unique tails: ``from`` is a keyword, so ``_from``)
    bfr = esco_inputs(_case("bfr_berlin"))
    assert (bfr["contract_signed__year"], bfr["contract_signed__month"], bfr["day"]) == (2006, 6, 1)
    assert (bfr["_from__year"], bfr["_from__month"], bfr["to__year"], bfr["to__month"]) == (2007, 4, 2016, 3)
    ehingen = esco_inputs(_case("ehingen_berufsschulzentrum"))
    assert (ehingen["_from__year"], ehingen["to__month"]) == (2010, 6)


def test_a_text_with_several_numbers_gives_each_later_number_as_its_own_input():
    # the first number keeps the name (the answer key's num reads it); the next are __2, __3, ...,
    # read by the answer key's own number pattern; a file path or line reference in the text is not one
    hungary = esco_inputs(_case("hungary_moe_school_espc_framework"))
    # 'at least HUF 11.1 billion (US$50 million) per year between 2006 and 2010 (der-vpp/.../...-2010.txt:2259-2260)'
    assert [hungary[k] for k in ("annual_goal", "annual_goal__2", "annual_goal__3", "annual_goal__4")] == \
        [Fraction("11.1"), 50, 2006, 2010]
    assert "annual_goal__5" not in hungary
    carpinteria = esco_inputs(_case("carpinteria_siemens_lease_purchase"))
    # '600 on the Effective Date; then 1,200 a year in arrears or 100 a month for a partial year (Escrow s.5.4)'
    assert (carpinteria["escrow_fees_usd__2"], carpinteria["escrow_fees_usd__3"]) == (1200, 100)
    assert "escrow_fees_usd__4" not in carpinteria                       # s.5.4 is a section
    # '1400 to 2536 euro = £1260 to £2280, by property type (STN L2102-2104)': a source line is no figure
    nottingham = esco_inputs(_case("nottingham_sneinton_energiesprong"))
    assert [nottingham[f"owner_contribution{s}"] for s in ("", "__2", "__3", "__4")] == [1400, 2536, 1260, 2280]
    assert "owner_contribution__5" not in nottingham
    # '170 projects, 45,8 (M) BGN funded or guaranteed, total value 67,6M BGN (CITY)': decimal commas
    eersf = esco_inputs(_case("eersf_aitos_hospital"))
    assert (eersf["fund_book_end_2014__2"], eersf["fund_book_end_2014__3"]) == (Fraction("45.8"), Fraction("67.6"))
    assert "fund_book_end_2014__4" not in eersf
    # '15 is common (WB08:10543)', '11 (Table CS12.1, WB08:10628-10640)': report codes, not figures
    iqara = esco_inputs(_case("iqara_energy_services_brazil"))
    assert "contract_length_years__2" not in iqara and "projects_listed__2" not in iqara


def test_a_citation_or_unit_is_not_an_input_even_when_it_holds_a_number():
    # ehingen's contract_signed.source quotes 'Vertragsschluss: 01.03.2009': a citation, not 1.03
    assert not [k for k in esco_inputs(_case("ehingen_berufsschulzentrum")) if k.endswith("__source")]


@pytest.mark.parametrize("oid", ORACLES)
def test_every_deal_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind" or oid in BLIND, r.reason
    if oid in RECORD_FINDINGS:
        assert r.verdict == "differs", r.reason
        assert {c["name"] for c in r.checks if not c["ok"]} == RECORD_FINDINGS[oid]
        assert r.planted_caught or oid in UNPLANTABLE, "the plant broke none of the checks that agree"
    elif oid in BLIND:
        assert r.verdict == "blind" and r.n_failed == 0, r.reason
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
