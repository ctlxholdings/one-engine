"""Averta's live TopCo: the sponsor's three streams, summed and split (plan step 8).

Referee: the live report's ``topco`` section at P50 and P90
(``engine_v2.runners.run_averta.build_topco``). The live TopCo is not an
entity run and has no time axis: it adds three numbers, the GP's total to
TopCo (nominal cash at the exit), the DevCo's hold net income (nominal) and
the DevCo's post-exit NPV (a present value at 8%), deducts five years of
admin, and splits the result 66/33/1 between its shareholders.

There is no TopCo case file: its figure is a view over other cases'
results, and it is reproduced here from them, the GP case
(``averta_gp.yaml``, which reads the HoldCo case) and the DevCo case
(``averta_devco.yaml``). The only live input not produced by a case is the
LTIP the DevCo receives, which the buyer step finds nil; it is added back
from the report to show the live figure is these cases' figure plus it.

What it adds (convergence count): nothing; no shared-module or pack change.

Findings pinned here (and in the LOG):

1. The "distributable" adds a present value (the DevCo's post-exit NPV,
   2.61M at P50) to nominal cash, and splits the sum between the
   shareholders as if it were cash.
2. Admin is charged for the hold only (75,000), though the figure carries
   twenty post-exit years of DevCo income.
3. TopCo carries the DevCo's hold loss (414,530.33) and has no cash before
   the GP's exit sweep, so nothing funds the DevCo's overdraft (DevCo
   finding 1) in the meantime.
"""
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
REPORT = json.loads((HERE / "reference" / "averta_live" / "report_data.json").read_text())
CFG = REPORT["config"]["proforma"]
HOLD, EXIT, PPY = 20, 20, 4
TOL = 1e-6


def gp_run(basis):
    case = load_yaml_strict((HERE / "averta_gp.yaml").read_text())
    if basis == "p90":
        case["generators"]["holdco"]["set"] = {"generators.portfolio.set": {
            "generators.revenue.specific_yield": REPORT["metadata"]["p90_specific_yield"]}}
    return run_case(case)


@pytest.fixture(scope="module")
def devco():
    return run_case(HERE / "averta_devco.yaml")


def streams(basis, devco):
    """The three streams the live TopCo adds, from the GP and DevCo cases, and the LTIP the live adds to the third."""
    gp = -sum(gp_run(basis).cut("to_topco").values, F(0))
    ni = devco.cut("net_income").values
    hold = sum(ni[:HOLD], F(0))
    disc = 1 + F(str(REPORT[basis]["post_exit_am"]["discount_rate"]))
    years = [sum(ni[EXIT + 1 + PPY * y:EXIT + 1 + PPY * (y + 1)], F(0)) for y in range(20)]
    npv = sum(x / disc ** (y + 1) for y, x in enumerate(years))
    after_tax = 1 - F(str(CFG["devco"]["corporate_tax_rate"]))
    ltip = sum(after_tax * F(str(l)) / disc ** (y + 1)
               for y, l in enumerate(REPORT[basis]["post_exit_am"]["annual_ltip_revenue"]))
    return gp, hold, npv, ltip


@pytest.mark.parametrize("basis", ["p50", "p90"])
def test_the_live_topco_is_the_gp_and_devco_cases_plus_the_ltip(basis, devco):
    tc = REPORT[basis]["topco"]
    gp, hold, npv, ltip = streams(basis, devco)
    assert abs(float(gp) - tc["gp_inflow"]) < TOL
    assert abs(float(hold) - tc["devco_inflow"]) < TOL
    assert abs(float(npv + ltip) - tc["post_exit_npv"]) < TOL
    admin = F(CFG["topco"]["admin_annual"]) / PPY * HOLD
    assert float(admin) == tc["total_admin"] == 75000
    distributable = max(F(0), gp + hold + npv + ltip - admin)
    assert abs(float(distributable) - tc["distributable"]) < TOL
    shares = [F(str(x)) for x in CFG["topco"]["ownership"]]
    assert sum(shares) == 1
    for s, live in zip(shares, tc["shareholder_distributions"]):
        assert abs(float(distributable * s) - live) < TOL


def test_finding_a_present_value_is_split_as_cash(devco):
    gp, hold, npv, ltip = streams("p50", devco)
    tc = REPORT["p50"]["topco"]
    # nearly half the figure is a present value, not cash anyone holds
    assert round(float(npv + ltip) / 1e6, 2) == 2.61
    assert 0.45 < float(npv + ltip) / tc["distributable"] < 0.55
    # without the LTIP (nil, buyer finding 5) the figure is 62,471.82 lower
    assert round(float(ltip), 2) == 62471.82


def test_finding_admin_stops_at_the_exit_though_the_figure_runs_twenty_years_on(devco):
    assert REPORT["p50"]["topco"]["total_admin"] == CFG["topco"]["admin_annual"] * HOLD / PPY
    ni = devco.cut("net_income").values
    assert len(ni) - (EXIT + 1) == 80 and all(x > 0 for x in ni[EXIT + 1:])


def test_finding_topco_has_no_cash_to_carry_the_devco_through_the_hold(devco):
    b = devco.group.book("devco")
    low = min(b.balance("cash", t) for t in range(HOLD))
    gp = gp_run("p50")
    # the GP's only payment to TopCo is at the HoldCo's exit, its period 21 (the calendar's 20)
    paid = [t for t, x in enumerate(gp.cut("to_topco").values) if x]
    assert paid == [21] and round(float(low), 2) == -1372768.75
