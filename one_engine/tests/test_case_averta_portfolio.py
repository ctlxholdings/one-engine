"""Averta's live portfolio and exit value: 1,260 reference SPVs on a deployment schedule (plan step 8).

Referee: the live model's dual report (``cases/reference/averta_live/
report_data.json``, captured from 01-averta engine_v2 at ``cab622b``), its
``portfolio`` and ``valuation`` sections, at P50 and P90.

How the live model builds them (``engine_v2/runners/run_averta.py``):

* ``aggregate_portfolio``: the reference SPV is run once; a cohort of ``n``
  projects reaching COD in hold quarter ``c`` adds ``n`` x the SPV's period
  ``t - c + 1`` to hold quarter ``t``, from the SPV's COD on. The
  distributions are the SPV's distributable cash; the revenue and opex
  arrays are the SPV's revenue and opex ledgers' *closings*;
* ``compute_year1_noi``, ``compute_forward_noi``, ``compute_model_ev``: the
  exit value is the buyer's first-year NOI, vintage-weighted, at a 10.5%
  cap rate (EV 134,092,595).

Here the portfolio is the ``cohorts`` generator (:mod:`one_engine.portfolio`,
new: the Portfolio and grain module, one core change) over the
``averta_spv.yaml`` template, and the value is the solar pack's
``rooftop_valuation`` generator on the template's own zones. The generator
specs are written here from the report's copy of the live configuration
(``config``), so each input is checked against it as it is written.

Findings pinned here:

1. The report's portfolio revenue, opex and NOI arrays are running totals
   (the SPV's P&L ledgers accumulate and are never closed), so its annual
   NOI of 127.2M in year 5 is a sum of four running totals; the year's
   actual NOI is 12.7M. Display only: HoldCo reads the distributions.
2. The P90 run's EV equals the P50 run's: the value path reads the zones'
   P50 yields, never the P90 basis (also in ``test_case_averta_spv.py``).
"""
import json
import pathlib

import pytest

from one_engine.cases import GENERATORS, run_case
from one_engine.inputs import Reader

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
REPORT = json.loads((HERE / "reference" / "averta_live" / "report_data.json").read_text())
CFG, PARAMS = REPORT["config"]["proforma"], REPORT["config"]["parameters"]
PPY = 4
HOLD = CFG["periods"]["hold_years"] * PPY
SCHEDULE = CFG["deployment"]["schedule_quarterly"]
TOL = 1e-6


def param(key):
    p = {x["key"]: x for x in PARAMS}[key]
    return next(p[k] for k in ("value", "mu", "mode") if p.get(k) is not None)


def gen(spec):
    r = Reader(spec, source="test")
    out = GENERATORS[r.take("kind")](r)
    r.finish()
    return out


def portfolio_spec(basis):
    spec = {
        "kind": "cohorts", "template": "averta_spv.yaml", "schedule": SCHEDULE, "periods": HOLD,
        "align": 1, "first": 1,
        "series": {"distributions": {"cut": "equity_flows", "times": -1},
                   "revenue": {"cut": "revenue", "running": True},
                   "opex": {"cut": "opex", "running": True},
                   "revenue_flow": {"cut": "revenue"}, "opex_flow": {"cut": "opex"}},
        "generated": {"zones": "sumproduct.zones"},
    }
    if basis == "p90":
        spec["set"] = {"generators.revenue.specific_yield": REPORT["metadata"]["p90_specific_yield"]}
    return spec


def valuation_spec(zones):
    kwp, a, d = param("kwp_installed"), CFG["asset"], CFG["devco"]
    am = d["am_variable_escalator"]
    return {
        "kind": "rooftop_valuation", "kwp": kwp, "calibration": param("pvgis_yield_correction"),
        "autoconsumo_ratio": a["autoconsumo_ratio"], "default_rate": param("consumer_default_rate"),
        "surplus_price": param("pool_compensation_price"), "zones": zones,
        "costs": {
            "insurance": {"annual": a["opex_insurance_per_kwp"] * kwp, "escalation": param("opex_escalation")},
            "rent": {"annual": a["rooftop_rent_annual"], "escalation": param("rooftop_rent_escalation")},
            "om_sub": {"annual": d["om_subcontractor_per_kwp"] * kwp, "escalation": am},
            "am_admin": {"annual": d["am_admin_per_kwp"] * kwp, "escalation": am},
            "am_margin": {"annual": d["am_margin_per_kwp"] * kwp, "escalation": am},
            "monitoring": {"annual": a["monitoring_per_kwp"] * kwp, "escalation": param("opex_escalation")},
        },
        "revenue_growth": {"degradation": param("degradation_rate"), "escalation": param("retail_tariff_escalation")},
        "schedule": SCHEDULE, "periods_per_year": PPY, "exit": HOLD, "cap_rate": CFG["exit"]["cap_rate"],
    }


@pytest.fixture(scope="module", params=["p50", "p90"])
def run(request):
    basis = request.param
    port = gen(portfolio_spec(basis))
    return basis, port, gen(valuation_spec(port["zones"]))


def close(xs, ys, tol=TOL):
    assert len(xs) == len(ys)
    worst = max(abs(float(x) - float(y)) for x, y in zip(xs, ys))
    assert worst <= tol, worst
    return worst


def test_the_portfolio_arrays_match_the_live_report(run):
    basis, port, _ = run
    q = REPORT[basis]["portfolio"]["quarterly"]
    close(port["distributions"], q["distributions"])
    close(port["revenue"], q["revenue"], 1e-5)
    close(port["opex"], q["opex"])
    close([r + o for r, o in zip(port["revenue"], port["opex"])], q["noi"], 1e-5)
    assert port["online"] == q["n_projects_online"]
    # the live model reads the inverter reserve's drawdown as maintenance capex; it falls at SPV
    # period 49, outside the 20-quarter hold, and the template never spends it
    assert q["maintenance_capex"] == [0.0] * HOLD


def test_the_annual_arrays_are_the_quarterly_ones_summed_by_year(run):
    basis, port, _ = run
    a = REPORT[basis]["portfolio"]["annual"]
    for key in ("distributions", "revenue", "opex"):
        close([sum(port[key][PPY * y:PPY * y + PPY]) for y in range(HOLD // PPY)], a[key], 1e-4)


def test_the_portfolio_counts_and_capex(run):
    basis, port, _ = run
    s = REPORT[basis]["portfolio"]["scalars"]
    assert port["count"] == s["total_projects"] == CFG["deployment"]["total_projects"] == 1260
    assert round(float(port["count"]) * param("kwp_installed") / 1000, 4) == s["total_mwp"]
    # each SPV buys its asset at the transfer price; the programme's capex is the count times it
    spend = run_case(HERE / "averta_spv.yaml").group.book("spv").balance("cwip", 0)
    assert abs(float(port["count"] * spend) - s["total_capex"]) < TOL
    assert abs(s["total_capex"] - REPORT[basis]["sources_uses"]["uses"]["total"]) < TOL


def test_the_exit_value_matches_the_live_report(run):
    basis, _, val = run
    v, spv = REPORT[basis]["valuation"], REPORT[basis]["spv"]["scalars"]
    assert abs(float(val["value"]) - v["ev"]) < TOL
    assert abs(float(val["portfolio_noi"]) - v["portfolio_noi"]) < TOL
    assert abs(float(val["forward_noi"]) - v["post_am_noi_per_project"]) < 1e-9
    am = sum(val["year1_costs"][k] for k in ("om_sub", "am_admin", "am_margin"))
    # the live "pre-AM" NOI adds the year-1 AM contract back to the forward-year NOI
    assert abs(float(val["forward_noi"] + am) - v["pre_am_noi_per_project"]) < 1e-9
    assert v["cap_rate"] == CFG["exit"]["cap_rate"] == 0.105
    assert abs(float(val["value"]) / (1260 * param("kwp_installed")) - v["ev_per_kwp"]) < 1e-9
    assert abs(float(val["year1_revenue"]) - spv["year1_revenue"]) < 1e-9
    assert abs(float(val["year1_noi"]) - spv["year1_noi"]) < 1e-9
    for k in ("insurance", "rent", "om_sub", "am_admin", "am_margin", "monitoring"):
        assert abs(float(val["year1_costs"][k]) - spv[f"year1_opex_{k}"]) < 1e-9


def test_finding_the_portfolio_revenue_and_opex_arrays_are_running_totals(run):
    basis, port, _ = run
    q = REPORT[basis]["portfolio"]["quarterly"]
    # the report matches the running totals (test above), not the quarters' own revenue and opex
    assert abs(float(port["revenue_flow"][0]) - q["revenue"][0]) < TOL  # the first quarter is both
    assert all(float(f) < r - 1 for f, r in zip(port["revenue_flow"][1:], q["revenue"][1:]))
    noi = [r + o for r, o in zip(port["revenue_flow"], port["opex_flow"])]
    year5_actual = float(sum(noi[16:20]))
    year5_report = REPORT[basis]["portfolio"]["annual"]["noi"][4]
    assert year5_report > 9 * year5_actual
    if basis == "p50":
        assert (round(year5_report / 1e6, 1), round(year5_actual / 1e6, 1)) == (127.2, 12.7)


def test_finding_the_p90_exit_value_is_the_p50_one():
    assert REPORT["p90"]["valuation"]["ev"] == REPORT["p50"]["valuation"]["ev"]
    p50, p90 = gen(portfolio_spec("p50")), gen(portfolio_spec("p90"))
    # the P90 basis lowers every distribution, but the value path reads the zones' P50 yields
    assert all(b < a for a, b in zip(p50["distributions"], p90["distributions"]))
    assert p50["zones"] == p90["zones"]
    assert gen(valuation_spec(p90["zones"]))["value"] == gen(valuation_spec(p50["zones"]))["value"]


def test_a_pnl_cut_is_the_template_s_income_less_expense_by_period():
    import copy
    from one_engine.inputs import InputError, load_yaml_strict
    spv = run_case(HERE / "averta_spv.yaml")
    book = spv.group.book("spv")
    rev = [sum(book.balance(a, t) - book.opening(a, t) for a in ("autoconsumo.revenue", "surplus.revenue"))
           for t in range(book.periods)]
    assert spv.cut("revenue").values == rev
    assert all(x <= 0 for x in spv.cut("opex").values) and sum(spv.cut("opex").values) < 0
    case = load_yaml_strict((HERE / "averta_spv.yaml").read_text())
    bad = copy.deepcopy(case)
    bad["cuts"]["revenue"]["kind"] = "accrual"
    with pytest.raises(InputError, match="unknown kind 'accrual'"):
        run_case(bad)
