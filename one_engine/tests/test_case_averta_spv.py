"""Averta's live reference SPV: one rooftop project, as 01-averta engine_v2 runs it (plan step 8).

Referee: the live model itself. ``consolidation/experiments/capture_averta_live.py``
runs 01-averta ``01_model/engine_v2`` at a named commit in a scratch copy and
stores, in ``cases/reference/averta_live/``, the full dual report the UI reads
(``report_data.json``) and the reference SPV's inputs and every ledger by
period (``spv_p50.json``). The report's SPV section (its scalars, quarterly
and annual arrays) and the ledgers are what one_engine must reproduce; they
are floats, so the test allows 1e-6 EUR (the books here are exact, and the
largest difference found is 2e-10).

The case file is written by ``make_case_averta_spv.py`` from the captured
inputs. This test re-derives each term from the live configuration (the
report's ``config`` copy of proforma.yaml and parameters.yaml) or checks it
against the captured inputs, so a fault in the generator shows as a
difference.

The revenue lines come from the solar pack's two generators, the live
revenue engine: ``zone_sumproduct`` (L0-L4: each zone's synthetic hourly
profile, the 3.0TD calendar and prices, their sumproduct) and
``rooftop_revenue`` (L5: the quarterly assembly). The live engine uses the
synthetic profile because the hourly PVGIS files are gitignored; with them
(on Ali's machine) its numbers differ (see the LOG). The P90 basis is the same case with a stated specific yield, checked
against the live P90 run (``spv_p90.json``).

What the case adds to the engine (convergence count 1): the case runner
runs asset generators (``generators``; ``{generator: name, output: x}`` in
the terms). Pack changes (not core): a ``capital_first`` distributor
returns one holder's capital before distributing the rest; the solar
pack's ``zone_sumproduct`` and ``rooftop_revenue`` generators. Reused
unchanged: the
offtake and operating cost contracts on payment terms (step 7), capex with
VAT refunded a period later, the funding plan's equity residual, the asset
register with a tax life (the nightmare case), income tax with a timing
register and deferred tax, the deposit account (step 7o) as the inverter
reserve, ``pay_as_due`` (step 7l), the waterfall's keep tier and its
sources, the cash flow cut and the irr measure.
"""
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine import measures as M
from one_engine.cases import run_case
from one_engine.inputs import InputError, State, load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "averta_spv.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
LIVE = HERE / "reference" / "averta_live"
SPV = json.loads((LIVE / "spv_p50.json").read_text())
SPV90 = json.loads((LIVE / "spv_p90.json").read_text())
REPORT = json.loads((LIVE / "report_data.json").read_text())
Q, S = REPORT["p50"]["spv"]["quarterly"], REPORT["p50"]["spv"]["scalars"]
CFG, PARAMS = REPORT["config"]["proforma"], REPORT["config"]["parameters"]
LEDGERS = SPV["ledgers"]
N, COD, END = CASE["periods"], 1, 99
OPEX = ("insurance", "rent", "om_sub", "am_admin", "am_margin", "monitoring")
LINES = ("autoconsumo", "surplus")
TOL = 1e-6


def param(key):
    p = {x["key"]: x for x in PARAMS}[key]
    # as the live engine reads a parameter: its value, else its mu, else its mode
    return next(p[k] for k in ("value", "mu", "mode") if p.get(k) is not None)


def mv(book, account, t):
    return book.balance(account, t) - book.opening(account, t)


def close(xs, ys, tol=TOL):
    """The largest difference between two series, which must be within ``tol``."""
    assert len(xs) == len(ys)
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst < tol, worst
    return worst


def variant(edit):
    case = copy.deepcopy(CASE)
    edit(case)
    return run_case(case)


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


@pytest.fixture(scope="module")
def book(res):
    return res.group.book("spv")


def revenue(book, t):
    return sum(mv(book, f"{x}.revenue", t) for x in LINES)


def opex(book, t, lines=OPEX):
    return sum(mv(book, f"{x}.expense", t) for x in lines)


def profit(book, t):
    """Net income: every profit-and-loss line of the book."""
    income = revenue(book, t)
    costs = opex(book, t) + sum(mv(book, a, t) for a in ("depreciation", "disposal_loss", "tax_expense",
                                                            "deferred_tax_expense"))
    return income - costs


# ── the copy of the inputs ────────────────────────────────────────────────

def test_the_case_file_copies_the_live_terms():
    c, kwp = CASE["contracts"], F(str(param("kwp_installed")))
    captured = SPV["inputs"]
    for line in captured["revenue_lines"]:
        terms = c[line["line_id"]]["terms"]
        assert terms["volume"] == {"generator": "revenue", "output": f"{line['line_id']}_volume"}
        assert terms["payment_days"] == CFG["asset"]["dso_days"] and terms["end"] == END
    g = CASE["generators"]["revenue"]
    assert F(g["kwp"]) == kwp and F(g["calibration"]) == F(str(param("pvgis_yield_correction")))
    assert F(g["degradation"]) == F(str(param("degradation_rate"))) and F(g["curtailment"]) == F(str(param("curtailment_factor")))
    assert F(g["autoconsumo_ratio"]) == F(str(CFG["asset"]["autoconsumo_ratio"]))
    assert {k: F(v) for k, v in g["occupancy"].items()} == {
        "initial": F(str(param("consumer_occupancy_rate"))), "churn_annual": F(str(param("annual_churn_rate"))),
        "replacement_months": F(str(param("churn_replacement_time_months")))}
    assert F(g["tariff"]["escalation"]) == F(str(param("retail_tariff_escalation")))
    assert F(g["tariff"]["default_rate"]) == F(str(param("consumer_default_rate")))
    assert (F(g["surplus"]["price"]), F(g["surplus"]["escalation"])) == (
        F(str(param("pool_compensation_price"))), F(str(param("surplus_price_escalation"))))
    assert g["zones"] == {"generator": "sumproduct", "output": "zones"}
    # L0-L4: each allocated zone's latitude and monthly yields, and the live engine's prices
    sp, live = CASE["generators"]["sumproduct"], SPV["revenue"]
    alloc = {z: w for z, w in CFG["zone_allocation"].items() if w > 0}
    assert {z: F(x["weight"]) for z, x in sp["zones"].items()} == {z: F(str(w)) for z, w in alloc.items()}
    for z, x in sp["zones"].items():
        assert F(x["latitude"]) == F(str(live["zone_inputs"][z]["lat"]))
        assert [F(m) for m in x["monthly_yield"]] == [F(str(m)) for m in live["zone_inputs"][z]["monthly_yield_per_kwp"]]
    for k in ("peaje", "cargo", "market"):
        assert {int(p): F(v) for p, v in sp["prices"][k].items()} == {
            int(p): F(str(v)) for p, v in live["prices_3td_2026"][k].items()}
    assert F(sp["prices"]["discount_factor"]) == F(str(param("discount_factor")))
    assert F(sp["prices"]["margin"]) == F(str(live["prices_3td_2026"]["margin"]))


    rates = {"insurance": param("opex_escalation"), "monitoring": param("opex_escalation"),
             "rent": param("rooftop_rent_escalation")}
    for line in captured["cost_lines"]:
        terms, k = c[line["line_id"]]["terms"], line["line_id"]
        rate = F(str(rates.get(k, CFG["devco"]["am_variable_escalator"])))
        index = [F(x) for x in terms["index"]]
        assert index == [(1 + rate) ** (t // 4) for t in range(N)]
        close(index, line["escalation_index"], 1e-12)
        assert terms["payment_days"] == CFG["asset"]["dpo_days"] and (terms["start"], terms["end"]) == (COD, END)
    bases = {"insurance": CFG["asset"]["opex_insurance_per_kwp"] * kwp, "rent": F(str(CFG["asset"]["rooftop_rent_annual"])),
             "om_sub": CFG["devco"]["om_subcontractor_per_kwp"] * kwp, "am_admin": CFG["devco"]["am_admin_per_kwp"] * kwp,
             "am_margin": CFG["devco"]["am_margin_per_kwp"] * kwp, "monitoring": CFG["asset"]["monitoring_per_kwp"] * kwp}
    assert {k: F(c[k]["terms"]["annual"]) for k in OPEX} == {k: F(str(v)) for k, v in bases.items()}
    # the transfer price: (EPC + effective devex) x (1 + development margin)
    d = CFG["devco"]
    devex = F(d["dead_deal_cost"]) * (1 / F(str(d["success_rate"])) - 1) + d["dev_cost_per_project"]
    price = (F(str(CFG["project"]["capex_per_kwp"])) * kwp + devex) * (1 + F(str(CFG["project"]["dev_margin"])))
    assert [F(x) for x in c["asset"]["terms"]["schedule"]] == [price] and F(c["asset"]["terms"]["vat_rate"]) == F("0.21")
    # the inverter reserve: 60 EUR/kWp in equal parts from COD to the replacement year, inclusive
    event = COD + int(param("inverter_replacement_year")) * 4
    target = kwp * F(str(param("inverter_replacement_cost")))
    assert c["mmr"]["terms"]["close_at"] == event
    assert [F(x) for x in c["mmr"]["terms"]["schedule"]] == [target / (event - COD + 1) if COD <= t <= event else 0
                                                              for t in range(N)]
    rb = CASE["rulebooks"]
    assert rb["assets"]["life_periods"] == CFG["depreciation"]["book_life_years"] * 4
    assert rb["assets"]["tax"]["life_periods"] == CFG["depreciation"]["tax_dep_years"] * 4
    assert F(rb["tax"]["rate"]) == F(str(CFG["tax"]["corporate_rate"]))
    keep, = [x for x in rb["waterfall"]["tiers"] if "keep" in x]
    assert F(keep["keep"]) == price * F("0.21") and (keep["from"], keep["until"]) == (COD, END - 1)


def test_the_case_files_tariff_calendar_is_the_live_one():
    # every hour of 2026 in its 3.0TD period, from the case file's tables, against the live calendar
    cal = CASE["generators"]["sumproduct"]["calendar"]
    holidays = {dt.date.fromisoformat(str(d)) for d in cal["holidays"]}
    hours = ""
    for k in range(365):
        d = dt.date(2026, 1, 1) + dt.timedelta(days=k)
        kind = "D" if d.weekday() >= 5 or d in holidays else cal["day_type_by_season"][cal["season_by_month"][d.month - 1]]
        hours += "".join(str(p) for p in cal["period_by_hour"][kind])
    assert hours == SPV["revenue"]["calendar_3td_2026"]
    # the national holidays: nine fixed dates and Good Friday (Easter Sunday 2026 is 5 April)
    fixed = {(1, 1), (1, 6), (5, 1), (8, 15), (10, 12), (11, 1), (12, 6), (12, 8), (12, 25)}
    assert holidays == {dt.date(2026, m, d) for m, d in fixed} | {dt.date(2026, 4, 5) - dt.timedelta(days=2)}


# ── the revenue generators ────────────────────────────────────────────────

def test_the_sumproduct_reproduces_each_zones_live_rates_and_yields(res):
    for z, x in res.generated["sumproduct"]["zones"].items():
        live = SPV["revenue"]["zones"][z]
        close(x["quarterly_yield"], live["quarterly_kwp"], 1e-12)
        close([x["regulated_rate"], x["market_rate"]],
              [live["effective_regulated_rate"], live["effective_market_rate"]], 1e-15)
    # what the rates are: production-weighted prices, so they sit between the cheapest and dearest period
    P = SPV["revenue"]["prices_3td_2026"]
    reg = [(P["peaje"][p] + P["cargo"][p]) * 0.85 for p in P["peaje"]]
    for x in res.generated["sumproduct"]["zones"].values():
        assert min(reg) < x["regulated_rate"] < max(reg)
    for s in res.generated["sumproduct"]["period_shares"].values():
        assert sum(s.values()) == pytest.approx(1, abs=1e-12) and s[6] > 0 and s[1] > 0


def test_the_generator_reproduces_the_live_revenue_lines(res):
    out = res.generated["revenue"]
    for line in SPV["inputs"]["revenue_lines"]:
        k = line["line_id"]
        close(out[f"{k}_volume"], line["volume"], 1e-9)
        close(out[f"{k}_price"], line["price"], 1e-15)
    close(out["occupancy"], SPV["revenue"]["occupancy"], 1e-15)


def test_generated_terms_enter_the_books_as_derived(res):
    terms = res.group.contract("autoconsumo").terms
    assert terms["volume"].state == State.DERIVED and terms["volume"].source == "generator revenue.autoconsumo_volume"
    assert terms["payment_days"].state == State.STATED


def test_the_p90_basis_reproduces_the_live_p90_spv():
    def p90(case):
        case["generators"]["revenue"]["specific_yield"] = REPORT["metadata"]["p90_specific_yield"]
    r = variant(p90)
    assert r.failed_checks() == []
    for line in SPV90["inputs"]["revenue_lines"]:
        close(r.generated["revenue"][f"{line['line_id']}_volume"], line["volume"], 1e-9)
    b, L = r.group.book("spv"), SPV90["ledgers"]
    close([b.balance("cash", t) for t in range(N)], L["cash"]["closing"])
    close([b.balance("tax_payable", t) for t in range(N)], L["tax_payable"]["closing"])
    close([-mv(b, "equity.distributions", t) - (mv(b, "equity.capital", t) if t else 0) for t in range(N)],
          [-x for x in L["cash"]["movements"]["distribution_out"]])
    assert r.measures["unlevered_irr"] == pytest.approx(SPV90["scalars"]["spv_unlevered_irr"], abs=1e-12)
    assert r.measures["unlevered_irr"] == pytest.approx(REPORT["p90"]["spv"]["scalars"]["unlevered_irr"], abs=1e-12)


def test_a_generator_refuses_what_it_cannot_read():
    def unknown_output(case):
        case["contracts"]["surplus"]["terms"]["price"] = {"generator": "revenue", "output": "pool_price"}
    with pytest.raises(InputError, match="no earlier generator makes"):
        variant(unknown_output)
    def weights(case):
        case["generators"]["sumproduct"]["zones"]["sur"]["weight"] = "0.3"
    with pytest.raises(InputError, match="weights sum to"):
        variant(weights)
    def stress(case):                                   # the decomposed tariff stress is not built
        case["generators"]["revenue"]["tariff"]["regulated_escalation"] = "0.04"
    with pytest.raises(InputError, match="regulated_escalation"):
        variant(stress)


# ── the answers: the report's SPV section ─────────────────────────────────

def test_the_case_runs_clean_and_winds_down(res, book):
    assert res.failed_checks() == []
    for account in CASE["wind_down"]["spv"]:
        assert book.balance(account, END) == 0


def test_the_unlevered_irr(res):
    assert res.measures["unlevered_irr"] == pytest.approx(S["unlevered_irr"], abs=1e-12)
    assert round(S["unlevered_irr"], 4) == 0.1021


def test_the_intrinsic_irr_strips_the_development_and_am_margins(res, book):
    # the live definition: the transfer price without its development margin, and CFADS with the
    # AM margin's expense (not its payment) added back
    margin = 1 + F(str(CFG["project"]["dev_margin"]))
    flows = list(res.cut("unlevered_flows").values)
    flows[0] = flows[0] / margin
    flows = [x + mv(book, "am_margin.expense", t) for t, x in enumerate(flows)]
    assert (1 + M.irr(flows)) ** 4 - 1 == pytest.approx(S["intrinsic_irr"], abs=1e-12)
    assert round(S["intrinsic_irr"], 4) == 0.1184


def test_the_quarterly_profit_and_loss(book):
    close([revenue(book, t) for t in range(N)], Q["revenue"])
    close([-opex(book, t) for t in range(N)], Q["opex"])
    for k in OPEX:
        close([-mv(book, f"{k}.expense", t) for t in range(N)], Q[f"opex_{k}"])
    close([revenue(book, t) - opex(book, t) for t in range(N)], Q["noi"])
    close([mv(book, "depreciation", t) for t in range(N)], Q["depreciation"])
    # the report's tax line is the whole tax expense, current and deferred
    close([-mv(book, "tax_expense", t) - mv(book, "deferred_tax_expense", t) for t in range(N)], Q["tax"])


def test_the_quarterly_cash_flow(res, book):
    w = res.records("waterfall")
    close([w[t]["cfads"] if t in w else 0 for t in range(N)], Q["cfads"])
    # a distribution is a return of capital (the capital account falls) or a dividend (distributions fall)
    paid = [-(mv(book, "equity.capital", t) if t else 0) - mv(book, "equity.distributions", t) for t in range(N)]
    close(paid, Q["distributable"])
    close([-SPV["ledgers"]["cash"]["movements"]["distribution_out"][t] for t in range(N)], Q["distributable"])


def test_the_quarterly_balance_sheet(book):
    close([book.balance("fixed_assets", t) for t in range(N)], Q["net_fixed_assets"])
    close([book.balance("cash", t) for t in range(N)], Q["cash_balance"])
    close([sum(book.balance(f"{x}.receivable", t) for x in LINES) for t in range(N)], Q["accounts_receivable"])
    close([sum(book.balance(f"{x}.payable", t) for x in OPEX) for t in range(N)], Q["accounts_payable"])
    close([book.balance("equity.capital", t) for t in range(N)], Q["contributed_capital"])
    # retained earnings: the profit so far less what was distributed out of it
    re, out = F(0), []
    for t in range(N):
        re += profit(book, t) + mv(book, "equity.distributions", t)
        out.append(re)
    close(out, Q["retained_earnings"])


def test_the_live_ledgers_the_report_does_not_show(book):
    L = LEDGERS
    close([book.balance("vat_receivable", t) for t in range(N)], L["vat_receivable"]["closing"])
    close([book.balance("mmr", t) for t in range(N)], L["maintenance_reserve"]["closing"])
    close([book.balance("tax_payable", t) for t in range(N)], L["tax_payable"]["closing"])
    close([mv(book, "tax_expense", t) for t in range(N)], [-x for x in L["tax_expense"]["movements"]["current_tax"]])
    # one net deferred balance here; the live model keeps a liability and an asset
    close([book.balance("deferred_tax", t) for t in range(N)],
          [x - y for x, y in zip(L["deferred_tax_liability"]["closing"], L["deferred_tax_asset"]["closing"])])
    close([mv(book, "disposal_loss", t) for t in range(N)], [-x for x in L["disposal_pnl"]["movements"]["disposal_loss"]])


def test_the_annual_sums(book):
    year = lambda f, y: sum(f(t) for t in range(4 * y, 4 * y + 4))
    A = REPORT["p50"]["spv"]["annual"]
    close([year(lambda t: revenue(book, t), y) for y in range(25)], A["revenue"])
    close([year(lambda t: revenue(book, t) - opex(book, t), y) for y in range(25)], A["noi"])


# ── findings ──────────────────────────────────────────────────────────────

def test_finding_the_capex_vat_refund_waits_in_the_spv_until_the_end(res, book):
    # the live waterfall distributes each quarter's CFADS only, so the 18,392 refunded at COD is idle
    # for 98 quarters and paid out in the terminal sweep
    vat = F(CASE["contracts"]["asset"]["terms"]["schedule"][0]) * F("0.21")
    assert all(book.balance("cash", t) == vat for t in range(COD, END))
    def paid_at_cod(case):
        case["rulebooks"]["waterfall"]["tiers"] = [x for x in case["rulebooks"]["waterfall"]["tiers"] if "keep" not in x]
    early = variant(paid_at_cod)
    assert early.failed_checks() == []
    assert round(res.measures["equity_irr"], 4) == 0.0792 and round(early.measures["equity_irr"], 4) == 0.0984


def test_finding_the_inverter_reserve_is_never_spent(book):
    # the live S7 releases the reserve to cash at the replacement date and S4 is never told of it:
    # no replacement is bought, and the 7,447.20 is distributed with that quarter's cash
    event = CASE["contracts"]["mmr"]["terms"]["close_at"]
    assert book.balance("mmr", event - 1) == F(CASE["contracts"]["mmr"]["terms"]["schedule"][1]) * (event - COD)
    assert book.balance("mmr", event) == 0
    assert [t for t in range(N) if mv(book, "cwip", t) > 0] == [0]
    assert Q["maintenance_capex"][event] == pytest.approx(7447.2) and sum(Q["maintenance_capex"]) == pytest.approx(7447.2)


def test_finding_the_p50_spv_runs_on_the_tmy_yield_not_the_stated_p50(res):
    # build_reference_spv overrides the yield only when the stated basis differs from the zone-weighted
    # P50 by more than 1 kWh/kWp, so the P50 run keeps the TMY profiles' own yield (1,492.7) and only the
    # P90 run is scaled (to 1,443.0): the P90/P50 production ratio is 0.9667, not 1443/1488.2 = 0.9696
    tmy = res.generated["revenue"]["annual_yield"]
    assert round(float(tmy), 2) == 1492.68 and REPORT["metadata"]["p50_specific_yield"] == 1488.2
    p90 = SPV90["inputs"]["revenue_lines"][0]["volume"][1] / SPV["inputs"]["revenue_lines"][0]["volume"][1]
    assert round(p90, 4) == round(1443.0 / float(tmy), 4) == 0.9667


def test_finding_the_reports_year1_revenue_is_not_the_spvs(book):
    # the report's year-1 figures come from a second revenue path (compute_year1_noi), which does not
    # read the yield basis: it is the same at P50 and P90, and neither is the SPV's first year
    first = sum(revenue(book, t) for t in range(COD, COD + 4))
    assert round(float(first), 2) == 16773.42 and round(S["year1_revenue"], 2) == 17393.74
    assert REPORT["p90"]["spv"]["scalars"]["year1_revenue"] == S["year1_revenue"]
    assert REPORT["p90"]["valuation"]["ev"] == REPORT["p50"]["valuation"]["ev"]
