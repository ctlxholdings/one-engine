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
contradicts or whose inputs cannot produce a scored figure, which differs at
exactly the figures its translation's header explains (and is still planted:
the plant must break a figure that agrees); the rest are blocked.
"""
from fractions import Fraction

import pytest

from one_engine import oracles as O
from one_engine.oracles.published import (PublishedFamily, TRANSLATIONS, _printed, figures,
                                          published_inputs)

FAMILY = PublishedFamily()
ORACLES = FAMILY.oracles()
TRANSLATED = {p.stem for p in TRANSLATIONS.glob("*.yaml")}
#: translated cases whose receipt is ``differs`` because the source contradicts itself, with every
#: figure that differs (no other may); the cause is in the translation's header, and the oracle is never edited
SOURCE_FINDINGS: dict = {
    # BFAP's per-hectare totals are rounded for display before they are subtracted (oracle notes)
    "atlas_growsell_maize_breakeven": {"marge_brute"},
    "bfap_maize_bloedrivier_2024_25": {"gross_income_zar_per_ha", "total_variable_expenditure_zar_per_ha",
                                       "gross_margin_zar_per_ha"},
    # the cost table's lines sum to 560,400, not the printed 709,000; the margin cell is a typo (oracle notes)
    "atlas_growsell_onion_errors": {"marge_cycle", "total_des_charges"},
    "giz_parse_oignon": {"total_des_charges_fcfa", "marge_beneficiaire_fcfa"},
    # molasses at the annexure's 4.5% is 540 lakh against 900 printed (the printed row uses 7.5%)
    "cap_sugarmill_ncdc_solapur_2500tcd": {"income_year_2.molasses", "total_income_year_2"},
    # the six cost lines do not reconcile with the stated EBITDAR (oracle notes)
    "cell_franchise_jackinthebox_item19": {"ebitdar_at_auv"},
    # extension budgets whose printed totals are not the sum of their own printed lines (a dollar or
    # a few cents of display rounding carried into later totals); each translation's header names it
    "ext_broiler_maryland": {"net_income_over_variable_and_fixed_costs_usd", "net_income_over_variable_costs_usd",
                             "total_variable_and_fixed_cost_usd", "total_variable_costs_usd"},
    "ext_corn_iowa2025": {"harvest_machinery_section_total.variable_usd_per_acre"},
    "ext_dairy_heifer_24month_isu": {"subtotal_before_ownership_and_labour_usd", "total_cost_24_months_usd",
                                     "total_feed_cost_usd", "weaning_to_calving_cost_usd"},
    "ext_freshwater_shrimp_100day_uky": {"return_to_land_capital_and_management_usd",
                                         "return_to_operator_labor_land_capital_and_management_usd",
                                         "total_fixed_costs_usd", "total_long_term_fixed_cost_usd",
                                         "total_variable_and_fixed_costs_in_sensitivity_table_usd"},
    "ext_hay_tamu2023": {"enterprise_total_100_acres.returns_above_specified_costs",
                         "enterprise_total_100_acres.total_fixed_costs",
                         "enterprise_total_100_acres.total_specified_costs",
                         "enterprise_total_100_acres.total_variable_costs"},
    "ext_maple_ohio2017": {"total_cost_oil.per_gal_usd", "total_fixed_cost_oil.per_gal_usd",
                           "total_variable_cost_oil.per_gal_usd"},
    "ext_table_olive_establish_ucd2023": {"accumulated_net_cash_costs_per_acre_usd.year2",
                                          "accumulated_net_cash_costs_per_acre_usd.year3",
                                          "accumulated_net_cash_costs_per_acre_usd.year4",
                                          "establishment_cost_40_acres_usd", "establishment_cost_per_acre_usd",
                                          "total_accumulated_net_cost_per_acre_usd.year6"},
    "ext_yearlingsteer_iowa2024": {"total_of_all_costs_usd", "total_variable_costs_usd"},
    "iowa_state_farrow_to_finish": {"profit_loss_usd_per_hd", "sales_value_usd_per_hd"},
    "landbank_beef_cattle_18month": {"gross_margin_R_per_LSU", "gross_margin_R_per_cow", "gross_production_value_R",
                                     "gross_production_value_R_per_LSU", "gross_production_value_R_per_cow"},
    # printed changes that do not follow from the printed GWh pairs (oracle notes)
    "energy_epra_kenya_2023h2": {"driver half_on_half_changes_pct.hydro", "driver half_on_half_changes_pct.wind"},
    # the on-grid / off-grid split is not reproducible from the source's own inputs (oracle notes)
    "energy_nigeria_diesel_captive_power": {"driver generation_off_grid_gwh_2012", "driver generation_on_grid_gwh_2012",
                                            "driver generation_total_gwh_2012"},
    # the workbook's funding circularity was never solved (three debt sizes); the year-5 revenue
    # needs a toll goal-sought to a 23% equity IRR that only the question text carries
    "express_fiji_toll_road_pf": {"revenue_nominal_year_5_kgbp", "spot_check_year_1.equity_injection_kgbp",
                                  "spot_check_year_20.debt_balance_kgbp", "total_investment_kgbp_year1"},
    # 4 x 2,695 is 10,780, printed 10,708
    "fao_ruralinvest_broiler_poultry": {"total_cost_per_unit_per_year"},
    # 38.67% printed "39%"; the oracle's 0.01 tolerance is for dollars, not a whole-percent rounding
    "fc_openstax_cbs_merchandising_income": {"gross_profit_margin_ratio.result_as_percent"},
    # ERRORS 2 to 5 of the oracle's own notes
    "fc_openstax_propensity_co_cashflow": {
        "direct_method_cash_flow_statement_as_printed_usd.cash_balance_dec_31_2018",
        "direct_method_cash_flow_statement_as_printed_usd.financing_activities.net_cash_flow_financing_activities",
        "direct_method_cash_flow_statement_as_printed_usd.operating_activities.net_cash_flow_operating_activities",
        "direct_method_cash_flow_statement_as_printed_usd.total_cash_flow_increase",
        "direct_method_supporting_calculations_usd.cash_paid_for_salaries.equals_cash_paid_for_salaries_as_printed",
        "financing_activities_alternate_as_printed_later_same_page_usd.net_cash_flow_financing",
        "financing_activities_alternate_as_printed_later_same_page_usd.payment_of_notes_payable_principal"},
    # ERROR 3 of the oracle's notes: the receivables line printed with the wrong sign
    "fc_openstax_virtual_co_cashflow": {"indirect_method_cash_flow_statement_usd.operating_activities."
                                        "accounts_receivable_change_as_labeled"},
    # the printed table breaks its own rules: year-1 miscellaneous of 4,000 on nil revenue (10% rule),
    # year-4 sales of 45,046 against 45,346 every other year, feed matching no stated head count;
    # every total and the IRR and NPV inherit them
    "ifad_zambia_poultry_esilp": {
        "financial_internal_rate_of_return_pct", "net_income_K", "npv_at_8pct_K", "operating_feed_broilers_K",
        "operating_feed_chicks_K", "operating_miscellaneous_K", "sales_live_animals_K", "total_operating_costs_K",
        "total_revenues_K", *(f"{row}.{y}" for row in ("net_incremental_benefits_K", "return_to_labour_K_per_day",
                                                         "total_costs_K") for y in range(1, 6))},
    # US$3.68/oz x 6,313 koz is 23,231.8, printed 23,206; the after-tax IRR needs annual cash flows
    # that no input carries
    "mining_mt_todd_2024_fs": {"after_tax_irr_pct", "cash_flow_summary_usd_000.refinery_costs"},
    # equipment printed 327,000 against its lines' 90,500; working capital leaves out the cattle
    "rte_embouche_bovine_niger_3cycles": {"cout_global_investissements_xof", "total_equipements_xof",
                                          "total_fonds_de_roulement_3mois_xof"},
    # cost per ram printed 45,960 against 54,960; the batch's depreciation charged to one ram
    "rte_embouche_ovine_niger_120j": {"amortissement_par_ovin_xof", "prix_de_revient_dun_ovin_xof",
                                      "prix_total_dun_ovin_xof"},
    # "about 726" against 725.76, outside the oracle's 0.05 lb
    "sep_msstate_p3489_beef_cutout": {"chilled_carcass_weight_lb"},
    # 418 printed against 417.96, the weight the sheet's own last line divides by
    "sep_msu_grassfed_freezerbeef_pricing": {"driver final_packaged_weight_lb"},
    # the per-beneficiary rows divide by 1,000 and 250 where the stated counts give 1,200 and 200
    "svc_clinic_ruralinvest": {
        "annual_operating_cost_per_all_beneficiaries_usd", "fixed_cost_per_all_beneficiaries_usd",
        "investment_donated_per_all_beneficiaries_usd", "investment_donated_per_direct_beneficiary_usd",
        "investment_own_resources_per_all_beneficiaries_usd", "investment_own_resources_per_direct_beneficiary_usd",
        "total_investment_per_all_beneficiaries_usd", "variable_cost_per_all_beneficiaries_usd"},
    # a dating convention, not an amount: the engine records an expiring loss in the last period that
    # can use it, as ARISE (golds/arise_expiry) and ModelOff 2015 Q39 date it; this oracle (the old
    # tax engine) records it in the first period that cannot. Cash tax and relief forgone agree
    "taxeng_loss_carryforward_expiry": {"losses_expired"},
}
#: translated cases whose scored figures the oracle's inputs cannot produce (they report "not
#: reported"), with every such figure: the inputs, not the engine, are missing what they need
INPUT_GAPS = {
    # annual reports transcribed without the balance sheet or cash flow lines behind the totals
    "agro_palmoil_okomu_ng_2022": {"cash_and_cash_equivalents_at_31_december",
                                   "cash_and_cash_equivalents_at_31_december.prior_year",
                                   "total_assets", "total_assets.prior_year", "total_equity",
                                   "total_equity.prior_year", "total_liabilities", "total_liabilities.prior_year"},
    "agro_poultry_astral_foods_za_2024": {"gross_profit.prior_year_restated", "net_change_in_cash",
                                          "total_assets", "total_assets.prior_year", "total_equity",
                                          "total_equity.prior_year", "total_liabilities",
                                          "total_liabilities.prior_year"},
    "agro_sugar_hippo_valley_zw_2020": {"cash_and_cash_equivalents_at_end_of_year", "total_assets",
                                        "total_equity"},
    "agro_sugar_zambia_sugar_2024": {"gross_profit.prior_year", "net_change_in_cash", "total_assets",
                                     "total_assets.prior_year", "total_equity", "total_equity.prior_year",
                                     "total_liabilities", "total_liabilities.prior_year"},
    # no revenue, units or platform share among the inputs
    "cell_indiegame_eastshade": {"net_revenue_post_platform"},
    # the 1995 residual value and the short-term loan's interest are not inputs, and the FRR needs both
    "fao_bl811e_beta_ltd_oilmill": {"cash_flow_from_operations_usd000", "financial_rate_of_return_pretax_prefinancing_pct",
                                    "investment_cost_stream_usd000", "net_cash_flow_usd000"},
    # IRENA's LCOE uses a country WACC and O&M basis the inputs do not carry
    "energy_irena_hydropower_2023": {"lcoe_usd_per_kwh_2023"},
    "energy_irena_solar_pv_2023": {"lcoe_usd_per_kwh_2023"},
    # net revenue needs grams per troy ounce, which no input carries and a translation may not type
    "mining_spanish_mountain_2021_pfs": {"cash_flow_summary.rows.17.3"},
}
#: differing cases whose plant cannot be seen, with why
UNPLANTABLE = {
    "cell_franchise_jackinthebox_item19": "the one money figure already differs; the oracle's single abs "
                                          "tolerance of 5,000 cannot fail its percentage",
    "cell_indiegame_eastshade": "1% of the cost is 2,000, inside the oracle's 50,000 tolerance",
    "energy_irena_hydropower_2023": "its one compared figure differs, so nothing that agreed can break",
    "energy_irena_solar_pv_2023": "its one compared figure differs, so nothing that agreed can break",
    "energy_nigeria_diesel_captive_power": "every compared figure differs, so nothing that agreed can break",
}


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
    known = {**SOURCE_FINDINGS, **INPUT_GAPS}
    if oid in known:
        assert r.verdict == "differs", r.reason
        assert {c["name"] for c in r.checks if not c["ok"]} == known[oid]
        assert r.planted_caught or oid in UNPLANTABLE, "the plant broke none of the checks that agree"
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
    folder.mkdir(parents=True)
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


def test_a_decimal_typed_into_a_translation_fails_the_gate_too(tmp_path):
    # the strict loader keeps a typed decimal exact (a Decimal, not a float): the gate must still see it
    fam = _planted_copy(tmp_path, "atlas_broker_sawdust",
                        "price: {generator: src, output: vente_sciures}", "price: 900000.50")
    r = O.receipt(fam, "atlas_broker_sawdust", commit="test")
    gate = next(c for c in r.checks if c["name"] == "translation traced")
    assert r.verdict == "differs" and not gate["ok"] and "900000.50" in gate["got"]


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


def test_a_comparative_printed_under_a_scored_figure_may_be_set_aside(tmp_path):
    # the library scores Airtel's revenue; the prior year printed beside it is a comparative the
    # one-year inputs cannot produce, and the scored value itself still cannot be set aside
    r = O.receipt(FAMILY, "svc_telecom_airtel_africa", commit="test")
    assert r.verdict == "matches" and "revenue_usd_m.prior_year_usd_m" in r.inputs.get("not_compared", {})
    fam = _planted_copy(tmp_path, "svc_telecom_airtel_africa", "  revenue_usd_m: {cut: revenue}\n", "",
                        more=[("not_compared:\n", "not_compared:\n  revenue_usd_m: not modelled\n")])
    r = O.receipt(fam, "svc_telecom_airtel_africa", commit="test")
    assert r.verdict == "differs" and "scored figures set aside ['revenue_usd_m']" in r.reason


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
