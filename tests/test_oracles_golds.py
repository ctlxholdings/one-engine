"""The golds library run through the oracle harness, and the pieces it relies on.

Each way a gold receipt could be earned dishonestly is planted here and must
be caught:

* a number typed into a translation instead of read from the gold fails
  the ``translation traced`` check;
* a gold trace row the translation does not map is a failed check, not a
  skip;
* a gold without a translation is ``blocked``, never ``matches``.

Then every hand gold gets its recorded verdict: those with a translation
match with the planted error caught, except a gold that contradicts itself,
which differs at the row its translation's notes explain; the rest are
blocked.
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
#: every translated gold must match; every other hand gold is blocked until translated
TRANSLATED = {p.stem for p in TRANSLATIONS.glob("*.yaml")}
#: translated golds whose receipt is ``differs`` because the gold contradicts itself, with the row
#: it is first seen at; the cause is in the translation's notes, and the oracle is never edited
GOLD_FINDINGS = {"importer_prepayment": "inv_active_opening"}


def test_every_hand_gold_is_vendored_and_in_the_manifest():
    assert len(ORACLES) == 32
    files = O.load_manifest()
    for oid in ORACLES:
        assert f"golds/{oid}.yaml" in files
        O.verify(f"golds/{oid}.yaml")


def test_the_inventory_rows_of_the_hand_golds_are_claimed():
    # the inventory names a hand gold by its path inside the golds library (``cases/<id>.yaml``), with the
    # library as its system; a pilot gold (``pilot/<id>.yaml``) and a published one are not hand golds
    row = {"system": "golds (Mac: one-place)", "encoding_id": "g_hand_gold", "path": "cases/arise.yaml"}
    assert FAMILY.inventory_oid(row) == "arise"
    assert FAMILY.inventory_oid({**row, "path": "pilot/sheep_tabaski.yaml"}) is None
    assert FAMILY.inventory_oid({**row, "path": "cases/not_a_gold.yaml"}) is None
    assert FAMILY.inventory_oid({**row, "encoding_id": "g_published_case", "path": "cases/golds/x.yaml"}) is None


@pytest.mark.parametrize("oid", ORACLES)
def test_every_hand_gold_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    assert r.verdict != "blind", r.reason
    if oid in GOLD_FINDINGS:
        assert r.verdict == "differs" and r.first_difference["name"] == GOLD_FINDINGS[oid], r.reason
    elif oid in TRANSLATED:
        assert r.verdict == "matches", r.reason
        assert r.planted_caught and r.n_checks > 3
    else:
        assert r.verdict == "blocked" and "no translation yet" in r.reason, (r.verdict, r.reason)


def _planted_copy(tmp_path, oid, old, new, more=()):
    """A copy of one translation with ``old`` replaced by ``new`` (and each further pair in ``more``)."""
    folder = tmp_path / "golds"
    folder.mkdir()
    text = (TRANSLATIONS / f"{oid}.yaml").read_text()
    for a, b in ((old, new), *more):
        assert text.count(a) == 1, a
        text = text.replace(a, b)
    (folder / f"{oid}.yaml").write_text(text)
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


def test_formulas_take_remainders_whole_powers_and_lags():
    out = _gen({"month": "t % 2", "grown": "base * (1 + g) ** t", "prior": "lag(grown, 1)", "two_back": "lag(base, 2)"},
               {"base": 100, "g": 0.1})
    assert out["month"] == [0, 1, 0]
    assert out["grown"] == [100, 110, 121]                              # exact: (11/10)**2 * 100
    assert out["prior"] == [0, 100, 110] and out["two_back"] == [0, 0, 100]


def test_formulas_take_running_totals():
    out = _gen({"landed": "(t % 2 == 0)", "to_date": "cumsum(landed)", "last_two": "to_date - lag(to_date, 2)",
                "flat": "cumsum(base)"}, {"base": 5}, periods=4)
    assert out["to_date"] == [1, 1, 2, 2] and out["last_two"] == [1, 1, 1, 1] and out["flat"] == [5, 10, 15, 20]


def test_formulas_refuse_an_unknown_name_and_a_reused_one():
    with pytest.raises(InputError, match="neither an input nor an earlier value"):
        _gen({"a": "b * 2"}, {"c": 1})
    with pytest.raises(InputError, match="already an input"):
        _gen({"c": "c * 2"}, {"c": 1})


# ── pieces the golds added, each shown to matter on a planted copy ─────────

def test_a_loss_carried_forward_is_not_a_loss_refunded(tmp_path):
    # the grain trader's loss months give a tax credit; carried forward instead, the tax row differs
    fam = _planted_copy(tmp_path, "grain", "      loss_relief: refund\n", "")
    r = O.receipt(fam, "grain", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "tax"


def test_stock_lost_in_store_is_its_own_line(tmp_path):
    # without the write-down the held balance books the loss as cost of sales
    fam = _planted_copy(tmp_path, "grain_shrinkage", "      expense: shrinkage_loss\n      amount: {generator: gold, output: shrinkage}\n",
                        "      expense: shrinkage_loss\n      amount: 0\n")
    r = O.receipt(fam, "grain_shrinkage", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] in ("cogs", "shrinkage_loss")


def test_a_row_per_head_divides_by_the_gold_input(tmp_path):
    fam = _planted_copy(tmp_path, "attieke_biogaz", "fcff_per_woman: {cut: fcff, per: gold.workers_count}",
                        "fcff_per_woman: {cut: fcff}")
    r = O.receipt(fam, "attieke_biogaz", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "fcff_per_woman"


def test_a_lagged_calendar_is_not_a_same_month_one(tmp_path):
    # the driller's wells are handed over cycle_months after they start; same month, the books differ
    fam = _planted_copy(tmp_path, "contractor_retention", "handovers: lag(starts, cycle_months - 1)", "handovers: starts")
    r = O.receipt(fam, "contractor_retention", commit="test")
    assert r.verdict == "differs"


def test_a_business_started_cold_is_not_one_in_steady_state(tmp_path):
    # no warm-up: the gold's first month already carries receivables and retentions; from nothing it does not
    fam = _planted_copy(tmp_path, "contractor_retention", "warm_up: 24\n", "warm_up: 0\n",
                        more=[("  periods: 60\n  phases", "  periods: 36\n  phases"),
                              ("      periods: 60\n", "      periods: 36\n")])
    r = O.receipt(fam, "contractor_retention", commit="test")
    assert r.verdict == "differs"


def test_a_warm_up_that_does_not_fit_the_periods_is_refused(tmp_path):
    fam = _planted_copy(tmp_path, "contractor_retention", "warm_up: 24\n", "warm_up: 12\n")
    r = O.receipt(fam, "contractor_retention", commit="test")
    assert r.verdict != "matches" and "periods" in r.reason


def test_a_transfer_left_out_is_seen(tmp_path):
    # retentions withheld but never released: cash and the retention balance both move
    fam = _planted_copy(tmp_path, "contractor_retention", "amount: {generator: gold, output: released}", "amount: 0")
    r = O.receipt(fam, "contractor_retention", commit="test")
    assert r.verdict == "differs"


def test_an_opening_injection_is_not_the_running_balance(tmp_path):
    fam = _planted_copy(tmp_path, "contractor_retention", "receivable_opening_injection: {carried_in: situations_receivable}",
                        "receivable_opening_injection: {balance: situations_receivable}")
    r = O.receipt(fam, "contractor_retention", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "receivable_opening_injection"


def test_a_salvage_value_is_not_depreciated(tmp_path):
    # without the residual the plant is depreciated to nil and the salvage sale books a gain
    fam = _planted_copy(tmp_path, "arise", "      residual: {generator: gold, output: salvage}\n", "")
    r = O.receipt(fam, "arise", commit="test")
    assert r.verdict == "differs"


def test_a_row_times_a_rate_multiplies_by_the_gold_input(tmp_path):
    fam = _planted_copy(tmp_path, "arise_expiry", "{record: tax.expired, times: gold.tax_rate}", "{record: tax.expired}")
    r = O.receipt(fam, "arise_expiry", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "tax_shield_forgone"


def test_a_pool_lives_its_mean_life_not_a_whole_number_of_years(tmp_path):
    # the cylinders' mean life is 12.5 years; cut to a whole 12, the charge is too high
    fam = _planted_copy(tmp_path, "lpg_deposits", "        mean_life: 1 / cylinder_loss_rate\n",
                        "        mean_life: 1 / cylinder_loss_rate\n        whole_life: 12\n",
                        more=[("      to: pool_6kg\n      expense: dep_cyl6\n      gross: {generator: gold, output: gross_6kg}\n"
                               "      life_periods: {generator: gold, output: mean_life}",
                               "      to: pool_6kg\n      expense: dep_cyl6\n      gross: {generator: gold, output: gross_6kg}\n"
                               "      life_periods: {generator: gold, output: whole_life}")])
    r = O.receipt(fam, "lpg_deposits", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "dep_cyl6"


def test_the_lower_tax_bands_are_relieved(tmp_path):
    # IBICA at the top rate on everything, without the two lower bands, is too much tax
    fam = _planted_copy(tmp_path, "paygo_solar",
                        "rate: {generator: gold, output: ibica_band1_rate}}",
                        "rate: {generator: gold, output: ibica_band3_rate}}",
                        more=[("rate: {generator: gold, output: ibica_band2_rate}}",
                               "rate: {generator: gold, output: ibica_band3_rate}}")])
    r = O.receipt(fam, "paygo_solar", commit="test")
    assert r.verdict == "differs" and r.first_difference["name"] == "tax"
