"""Baghara's workbooks through the oracle harness, and the reader that reads them.

The reader names every input and figure mechanically, so the ways it could
pass a workbook dishonestly are each pinned: a number typed inside a formula
is an input like a typed cell; a formula cell that saved no number is set
aside by the oracle with what it saved; IRR, XIRR and RATE cells keep Excel's
documented 1e-7 and every other cell the drift of double arithmetic; a figure
the translation neither reads nor sets aside fails as "not reported".

Then every workbook gets its recorded verdict: a translated workbook
matches, or differs on exactly the figures its own formula slips (or linked
files that disagree) get wrong, each worked out in its translation's header;
the planted error is caught; a workbook with no translation yet is blocked,
saying so.
"""
from fractions import Fraction

import pytest
import yaml

from one_engine import oracles as O
from one_engine.oracles.workbook import (FOLDER, ITERATIVE_TOL, WorkbookFamily, _literals, read_workbook,
                                         sheet_key)

FAMILY = WorkbookFamily()
ORACLES = FAMILY.oracles()
#: the workbooks vendored from Baghara (02-Financial-Models and 03_evidence/promoter-originals, and v15.24
#: from the golds corpus), the ModelOff answer and data workbooks, Bodmer's solar case (a macro workbook,
#: .xlsm) and Macabacus's two LBO templates
VENDORED = {"nour_3moissons_haraz", "van_10ans_previsions_3moissons", "cap_3moissons_estimation_2025",
            "business_plan_3moissons_2021", "cattle_integration_financial_model", "haraz_financial_model_2013",
            "baghara_v15_24", "modeloff_2012_asset_schedule", "modeloff_2012_investment_property",
            "modeloff_2013_hard_times", "modeloff_2014_time_is_money", "modeloff_2015_more_money_please",
            "modeloff_2015_tax_solution", "bodmer_solar_case_study", "macabacus_lbo_short", "macabacus_lbo_long"}
#: the figures each translated workbook gets wrong by its own formula slips, or by linked files that
#: disagree with each other (each translation's header works every one out); the workbook wins
SLIPS = {
    "nour_3moissons_haraz": {"VAN_Nour_Baghara.C54:F54", "VAN_Nour_Baghara.C55:F55", "VAN_Nour_Baghara.C46:W46",
                             "VAN_Nour_Baghara.C47:W47"},
    "van_10ans_previsions_3moissons": {
        # PL row 33 takes the later cohorts' overheads (rows 90, 117) for their one-off charges (89, 116)
        "PL.C33:J33", "PL.C40:J40", "PL.C43:J43", "PL.C47:J47", "_50_CAP.C37:J37",
        "VAN_des_CAP.D43:O43", "VAN_des_CAP.D46:O46", "VAN_des_CAP.D47:O47",
        "VAN_des_CAP.C48", "VAN_des_CAP.C49", "VAN_des_CAP.C51", "VAN_des_CAP.C52",
        # Invest I38:J38 repeat year 5's depreciation where the P&L charges the imported year-6 amount
        "Invest.C24:J24", "Invest.C25:J25", "Invest.I38:J38", "Invest.E49:J49",
        # the per-CAP loan balance is stitched from two linked files and is not draws less repayments
        "Emprunts.D30:K30", "Emprunts.F206:K206", "Emprunts.G223:K223", "_50_CAP.D109:J109"},
    "baghara_v15_24": {
        # 08_OpEx row 17, the DSRA LC fee, is charged but left out of the opex totals (rows 7 and 71);
        # with the fee set to nil every one of these agrees
        "_00_Cover.D24", "_00_Cover.D25", "_00_Cover.D26", "_08_OpEx.D10:L10", "_08_OpEx.D71:L71",
        "_08_OpEx.D7:L7", "_12_WC.D13:L13", "_12_WC.D15:L15", "_12_WC.D9:L9", "_13_Tax.D12:L12",
        "_13_Tax.D16:L16", "_13_Tax.D17:L17", "_13_Tax.D18:L18", "_13_Tax.D19:L19", "_13_Tax.D9:L9",
        "_14_IS.D13:L13", "_14_IS.D18:L18", "_14_IS.D22:L22", "_14_IS.D27:L27", "_14_IS.D8:L8", "_14_IS.D9:L9",
        "_15_BS.D13:L13", "_15_BS.D16:L16", "_15_BS.D20:L20", "_15_BS.D24:L24", "_15_BS.D26:L26",
        "_15_BS.D27:L27", "_15_BS.D8:L8", "_16_CFS.D11:L11", "_16_CFS.D13:L13", "_16_CFS.D26:L26",
        "_16_CFS.D28:L28", "_16_CFS.D29:L29", "_16_CFS.D8:L8", "_17_Outputs.D11:L11", "_17_Outputs.D14:L14",
        "_17_Outputs.D16:L16", "_17_Outputs.D18:L18", "_17_Outputs.D25", "_17_Outputs.D29:L29",
        "_17_Outputs.D30", "_17_Outputs.D34", "_17_Outputs.D35", "_17_Outputs.D36", "_17_Outputs.D37",
        "_17_Outputs.D38", "_17_Outputs.D8:L8", "_17_Outputs.D9:L9", "driver _17_Outputs.D22",
        "driver _17_Outputs.F22:L22",
        # 09_Debt D13's total is one year's interest, not the sum of the years
        "_09_Debt.D13:L13"},
    "business_plan_3moissons_2021": {
        # fourteen slips, each named with its cell and both numbers in the translation's header (S1-S14):
        # Dep: technical buildings taxed at 15% not 5% (S1), miscellaneous assets divided by 100,000 (S2),
        # straight line run past cost in year 10 (S3); these move the whole P&L below variable costs
        "Dep.B15:L15", "Dep.B17:L17", "Dep.B22:L22", "Dep.B23:L23", "Dep.B32:L32", "Dep.B38:L38",
        "Dep.B39:L39", "Dep.C20:L20", "Dep.C31:L31", "Dep.C34:L34", "Dep.C42:L42",
        "P_L.D36:M36", "P_L.D38:M38", "P_L.D40:M40", "P_L.D42:M42", "P_L.D45:M45", "P_L.D46:M46",
        "P_L.D47:M47", "P_L.D48:M48", "P_L.D50:M50", "P_L.D54:M54", "P_L.D55:M55", "P_L.D56:M56",
        "P_L.D57:M57", "P_L.D58:M58", "P_L.D60:L60", "P_L.D66:L66", "P_L.D67:L67", "P_L.D68:L68",
        "P_L.C75:L75", "P_L.D83:L83", "P_L.C84:L84", "P_L.D85:L85", "P_L.D92:M92", "P_L.D94:M94",
        "P_L.D95:M95", "P_L.D99:M99", "P_L.D100:M100", "P_L.D102:M102", "P_L.D103:M103", "P_L.D104:M104",
        "P_L.D109:L109", "P_L.D110:L110", "P_L.D111:L111", "P_L.D112:L112", "P_L.D113:L113",
        "P_L.D114:L114", "P_L.D115", "P_L.D118:L118", "P_L.D124:L124", "P_L.D125", "P_L.D129:L129",
        "P_L.D136:J136", "P_L.D137", "P_L.D142:L142", "P_L.D143:L143", "P_L.D145:L145", "P_L.C154:M154",
        "P_L.C155", "P_L.D159:L159", "P_L.D160:L160", "P_L.D162:L162", "P_L.C167:L167", "P_L.D176:L176",
        "P_L.D177:L177", "P_L.D178:L178", "P_L.D179:L179", "P_L.D181:L181", "P_L.D183:L183",
        "P_L.F199:L199", "P_L.D202:L202", "P_L.D204:L204", "P_L.F205:L205", "P_L.F214:L214",
        "P_L.D217:L217", "P_L.F218:L218", "P_L.D229:L229", "P_L.D230:L230", "driver P_L.D232:L232",
        "P_L.C239", "P_L.C240", "P_L.C241",
        # Growing cost: three crop totals add the hectares (S11-S13), aubergine margin subtracts the yield (S14)
        "Growing_cost_assum.B15:L15", "Growing_cost_assum.C20:L20", "Growing_cost_assum.B107:L107",
        "Growing_cost_assum.C112:L112", "Growing_cost_assum.C149:L149", "Growing_cost_assum.B198:L198",
        "Growing_cost_assum.C203:L203"},
    "modeloff_2015_tax_solution": {
        # not a slip: no reading yet tests a record for nonzero (the first month a loss expires);
        # the row is all zeros here and feeds only F104, a date answer the oracle sets aside
        "Solution.M62:EN62"},
}


def test_the_workbooks_are_vendored_and_in_the_manifest():
    assert set(ORACLES) == VENDORED
    for oid in ORACLES:
        O.verify(FAMILY.source(oid))
        assert FAMILY.source(oid) in (f"{FOLDER}/{oid}.xlsx", f"{FOLDER}/{oid}.xlsm")
    assert FAMILY.source("bodmer_solar_case_study").endswith(".xlsm")


def test_inputs_are_named_mechanically_and_a_number_inside_a_formula_is_one():
    assert sheet_key("Coûts de product° Agricole") == "Couts_de_product_Agricole"
    assert sheet_key("50 CAP") == "_50_CAP"
    assert _literals("=M29*1.5") == [Fraction(3, 2)]
    assert _literals("=1-10%") == [1, Fraction(1, 10)]
    assert _literals("=SUM(C5:C9)") == []
    book = read_workbook(FAMILY.reference / FOLDER / "nour_3moissons_haraz.xlsx")
    assert book["inputs"]["Hypotheses_Nour_3Moissons__N29__n1"] == Fraction(3, 2)
    assert "VAN_Nour_Baghara__C7_W7" in book["inputs"]                 # a typed row is also a series
    assert all(isinstance(v, (Fraction, list)) for v in book["inputs"].values())


def test_a_typed_date_is_an_input_by_its_excel_serial():
    # Excel stores a date as a day count (1 = 1 Jan 1900 in the 1900 system); a model computes with it
    # (days in a period, a day-weighted rate), so the date the modeller typed is a number they typed
    book = read_workbook(FAMILY.reference / FOLDER / "modeloff_2015_more_money_please.xlsx")
    assert book["inputs"]["Inputs__L12"] == 50586                       # 30 June 2038


def test_a_cell_read_from_another_workbook_is_an_input_with_the_value_it_imported():
    # '1 CAP'!D33 is =[2]PL!D27, a workbook that was not saved with this one: nothing here can recompute
    # it, so the value the workbook imported is what it computed from
    book = read_workbook(FAMILY.reference / FOLDER / "van_10ans_previsions_3moissons.xlsx")
    assert float(book["inputs"]["_1_CAP__E33"]) == pytest.approx(218.11174)
    assert isinstance(book["inputs"]["_1_CAP__D33_J33"], list)              # an imported row is a series too
    assert book["figures"]["_1_CAP.C33"] and not [f for f in book["figures"] if f.startswith("_1_CAP.D33")]
    assert all("[" not in t for fs in book["formulas"].values() for t in fs)


def test_a_formula_cell_that_saved_no_number_is_set_aside_by_the_oracle_with_what_it_saved():
    book = read_workbook(FAMILY.reference / FOLDER / "cattle_integration_financial_model.xlsx")
    assert book["aside"] and all(r.startswith("the workbook saved no number here") for r in book["aside"].values())
    assert not set(book["aside"]) & set(book["figures"])


def test_rate_cells_keep_excels_precision_and_the_rest_double_drift():
    fig = "VAN_Nour_Baghara.C55:F55"
    _, formulas = FAMILY._load("nour_3moissons_haraz")
    assert all("IRR(" in f for f in formulas[fig])
    v = Fraction(1, 10)
    assert FAMILY._within(fig, v, v + ITERATIVE_TOL, formulas, None)[0]
    assert not FAMILY._within(fig, v, v + 2 * ITERATIVE_TOL, formulas, None)[0]
    other = "VAN_Nour_Baghara.C24:W24"
    big = Fraction(10 ** 7)
    assert FAMILY._within(other, big, big + Fraction(9, 1000), formulas, None)[0]        # 1e-9 relative
    assert not FAMILY._within(other, big, big + Fraction(11, 1000), formulas, None)[0]
    assert not FAMILY._within(other, Fraction(0), Fraction(2, 10 ** 6), formulas, None)[0]  # 1e-6 floor


def _translation_copy(tmp_path, edit):
    src = FAMILY.translations / "nour_3moissons_haraz.yaml"
    tr = yaml.safe_load(src.read_text())
    edit(tr)
    (tmp_path / "nour_3moissons_haraz.yaml").write_text(yaml.safe_dump(tr, sort_keys=False))
    return WorkbookFamily(translations=tmp_path)


def test_a_figure_neither_read_nor_set_aside_fails_and_a_whole_sheet_may_be_set_aside(tmp_path):
    fam = _translation_copy(tmp_path, lambda tr: tr["figures"].pop("VAN_Nour_Baghara.C24:W24"))
    checks, _ = fam.run("nour_3moissons_haraz")
    gone = [c for c in checks if c.name == "VAN_Nour_Baghara.C24:W24"]
    assert len(gone) == 1 and gone[0].got == "not reported" and not gone[0].ok

    def sheet(tr):
        for f in [f for f in tr["figures"] if f.startswith("Hypotheses_Nour_Haraz.")]:
            del tr["figures"][f]
        tr["not_compared"] = {"Hypotheses_Nour_Haraz": "planted: the whole sheet set aside"}
    checks, record = _translation_copy(tmp_path, sheet).run("nour_3moissons_haraz")
    assert not [c for c in checks if c.name.startswith("Hypotheses_Nour_Haraz.")]
    assert record["not_compared"] == {"Hypotheses_Nour_Haraz": "planted: the whole sheet set aside"}

    def both(tr):
        tr["not_compared"] = {"VAN_Nour_Baghara": "planted: a sheet whose figures are also read"}
    with pytest.raises(ValueError, match="read and set aside"):
        _translation_copy(tmp_path, both).run("nour_3moissons_haraz")


@pytest.mark.parametrize("oid", ORACLES)
def test_every_workbook_gets_its_recorded_verdict(oid):
    r = O.receipt(FAMILY, oid, commit="test")
    if FAMILY.translation(oid) is None:
        assert r.verdict == "blocked" and r.reason.startswith("no translation yet"), r.reason
        return
    failing = {c["name"] for c in r.checks if not c["ok"]}
    if oid in SLIPS:
        assert r.verdict == "differs" and failing == SLIPS[oid], failing
    else:
        assert r.verdict == "matches", failing
    assert r.planted_caught
