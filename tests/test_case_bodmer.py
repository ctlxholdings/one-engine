"""Bodmer's 6MW solar Financing Model, rebuilt from its Operating Model's outputs (plan step 7e).

Referee: the workbook itself (``cases/reference/bodmer_solar.json``,
cached values of Edward Bodmer's Solar-Case-Study-Complete.xlsm, written by
``consolidation/experiments/make_reference_bodmer.py``). One_engine reads
only its inputs (EBITDA, development and EPC cost, rates, financing
assumptions, the sources and uses budget) and must reproduce 33 of the
Financing Model's rows, period by period, and its headline figures.

Tolerances: the workbook computes in binary floating point; one_engine
in exact fractions. Rows agree to 1e-8 in currency units (largest
difference seen 3.4e-9 on balances of 3.4 million); returns to 1e-8, the
precision Excel's XIRR iterates to.

What the case adds to the engine (convergence count): the Loop solver
(``loops.py``, new), dated returns (``measures.xirr``), and the case
runner's ``loops`` and ``xirr`` wiring. Pack changes (not core): series
day counts and fee rates, a loan principal schedule, the ``pro_rata_funding``
rulebook, and the ``sculpt`` and ``uses_total`` loop rules.
"""
import copy
import datetime as dt
import json
import pathlib
from decimal import Decimal
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import InputError, load_yaml_strict
from one_engine.loops import LoopError
from one_engine.measures import xirr
from one_engine.packs.contracts import _at, _days
from one_engine.packs.pf import SizingError, sculpt

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
CASE_PATH = HERE / "bodmer_6mw.yaml"
CASE = load_yaml_strict(CASE_PATH.read_text())
REF = json.loads((HERE / "reference" / "bodmer_solar.json").read_text())
I, O, S = REF["inputs"], REF["oracle"], REF["scalars"]
N = len(I["period"])
COD, MATURITY = 9, 88


@pytest.fixture(scope="module")
def res():
    return run_case(CASE_PATH)


def close_enough(a, b, tol=1e-8):
    return abs(float(a) - b) <= tol * max(1.0, abs(b) / 1e6)


# ── the copy of the workbook's inputs ─────────────────────────────────────

def test_the_case_file_copies_the_workbooks_inputs():
    c, rb = CASE["contracts"], CASE["rulebooks"]
    assert [F(x) for x in c["operations"]["terms"]["volume"]] == [F(Decimal(repr(x))) if isinstance(x, float)
                                                                    else F(x) for x in I["ebitda"]]
    assert c["development"]["terms"]["schedule"] + [0] * (N - 9) == I["development_cost"]
    assert c["epc"]["terms"]["schedule"] + [0] * (N - 9) == I["epc_cost"]
    senior = c["senior"]["terms"]
    assert senior["rate"] == [Decimal(repr(b)) + Decimal(repr(s)) for b, s in zip(I["base_rate"], I["credit_spread"])]
    assert senior["days_per_period"] == [30 * m for m in I["months"]]
    assert [float(x) for x in senior["commitment_fee_rate"]] == [I["commitment_fee_rate"] * c for c in I["construction"][:9]]
    assert [p for p in range(N) if I["repaying"][p]] == list(range(senior["repay_from"], MATURITY + 1))
    assert senior["repay_from"] + senior["repay_periods"] - 1 == MATURITY
    assert I["period"][senior["fee_at"]] and I["start"][senior["fee_at"]] == I["up_front_fee_date"]
    budget = rb["funding"]["budget"]
    assert {k: float(budget[k]) for k in ("idc", "up_front_fee", "commitment_fee")} == I["fixed_uses"]
    assert float(sum(budget.values())) == pytest.approx(S["total_uses"], abs=1e-9)
    assert CASE["measures"]["equity_irr"]["dates"] == I["end"]


# ── the loop ──────────────────────────────────────────────────────────────

def test_the_sizing_loop_closes_exactly_in_two_runs(res):
    rep = res.loops["debt_size"]
    # the first run (no debt) gives the CFADS; the second, sized, gives the same CFADS, as tax ignores interest
    assert rep.iterations == 2 and rep.residual == 0 and rep.history[1] == 0
    assert rep.values["debt"] == pytest.approx(S["debt_sized"], rel=1e-14)


def test_the_debt_is_the_service_discounted_at_the_loans_own_index(res):
    terms = res.group.contract("senior").values()
    rate = lambda t: _at(terms["rate"], t) * _days(terms, t)
    cfads = {t: r["cfads"] for t, r in res.records("waterfall").items()}
    debt, repayment, index = sculpt(cfads, rate, F("1.45"), COD, MATURITY, N - 1)
    assert close_enough(debt, S["debt_sized"])
    for t in range(N):
        assert close_enough(rate(t), O["periodic_rate"][t], 1e-15)
        assert close_enough(index.get(t, 1), O["rate_index"][t], 1e-14)
        assert close_enough(repayment.get(t, 0), O["sizing_repayment"][t])
        assert close_enough(cfads[t] / F("1.45") if COD <= t <= MATURITY else 0, O["target_debt_service"][t])
    assert sum(repayment.values()) == debt
    # PLCR at COD: all operating CFADS, discounted at the same index, over the sized debt
    plcr = sum(cfads[t] / index[t] for t in range(COD, N)) / debt
    assert close_enough(plcr, S["plcr_at_cod"], 1e-12)


# ── the Financing Model, row by row ───────────────────────────────────────

def _rows(res):
    b = res.group.book("spv")
    ev = lambda prefix, t: sum((p.amount for p in b.postings if p.event == f"{prefix}:{t}"), F(0))
    mv = lambda a, t: b.balance(a, t) - b.opening(a, t)
    owed = lambda t, read: read("senior.debt", t) + read("senior.capitalised_interest", t)
    fund, tax, wf = res.records("funding"), res.records("tax"), res.records("waterfall")
    f = lambda t, get: get(fund[t]) if t in fund else 0
    return {
        "debt_opening": lambda t: owed(t, b.opening),
        "debt_closing": lambda t: owed(t, b.balance),
        "debt_draw": lambda t: ev("senior:claim.draw", t),
        # principal paid less interest capitalised: the workbook's repayment, negative in six quarters
        "debt_repayment": lambda t: ev("senior:claim.principal", t) + ev("senior:claim.capitalised", t)
        - ev("senior:pik", t),
        "interest_cost": lambda t: mv("senior.interest", t),
        "interest_expense": lambda t: mv("senior.interest", t) * I["operating"][t],
        "idc": lambda t: mv("senior.interest", t) * I["construction"][t],
        "up_front_fee": lambda t: ev("senior:claim.arrangement_fee", t),
        "commitment_fee": lambda t: ev("senior:commitment_fee", t),
        "funding_needs": lambda t: f(t, lambda r: sum(r["uses"].values())),
        "equity_up_front": lambda t: f(t, lambda r: r["paid"]["up_front"]),
        "debt_pro_rata": lambda t: f(t, lambda r: r["paid"]["senior.draw"]),
        "equity_pro_rata": lambda t: f(t, lambda r: r["residual"]),
        "equity_funding": lambda t: ev("equity:leg.contribute", t),
        "ebt": lambda t: tax[t]["base"],
        "nol_closing": lambda t: tax[t]["losses"],
        "tax": lambda t: tax[t]["tax"],
        "cfads": lambda t: wf[t]["cfads"],
        "cfads_sizing": lambda t: wf[t]["cfads"] * I["operating"][t],
        "cash_after_debt_service": lambda t: wf[t]["residual"],
        "debt_service": lambda t: wf[t]["service"],
        "dscr": lambda t: wf[t]["dscr"] or 0,           # the workbook shows 0 where nothing is due
        "equity_cash_flow": lambda t: -res.cut("equity_flows").values[t],
        "project_cf_aftertax": lambda t: res.cut("project_flows").values[t],
    }


ROWS = ["debt_opening", "debt_closing", "debt_draw", "debt_repayment", "interest_cost", "interest_expense", "idc",
        "up_front_fee", "commitment_fee", "funding_needs", "equity_up_front", "debt_pro_rata", "equity_pro_rata",
        "equity_funding", "ebt", "nol_closing", "tax", "cfads", "cfads_sizing", "cash_after_debt_service",
        "debt_service", "dscr", "equity_cash_flow", "project_cf_aftertax"]


@pytest.mark.parametrize("row", ROWS)
def test_each_financing_model_row_matches_every_period(res, row):
    ours = _rows(res)[row]
    bad = [(t, float(ours(t)), O[row][t]) for t in range(N) if not close_enough(ours(t), O[row][t])]
    assert bad == []


def test_the_lenders_flows_match_except_the_minus_one_the_workbook_types():
    # K201 is a typed -1 at the first date, a device to anchor XIRR; nothing in the model pays it
    res = run_case(CASE_PATH)
    ours = [-x for x in res.cut("debt_flows").values]
    assert [t for t in range(N) if not close_enough(ours[t], O["debt_cash_flow"][t])] == [0]
    assert O["debt_cash_flow"][0] == -1
    dates = [dt.date.fromisoformat(d) for d in I["end"]]
    assert abs(xirr([-1] + ours[1:], dates) - S["debt_irr"]) < 1e-8
    assert abs(res.measure("debt_irr") - S["debt_irr"]) < 1e-7          # without it: 3.4e-8 apart


def test_the_headline_figures(res):
    assert abs(res.measure("equity_irr") - S["equity_irr"]) < 1e-8
    assert abs(res.measure("project_irr") - S["aftertax_project_irr"]) < 1e-8
    dscr = [res.records("waterfall")[t]["dscr"] for t in range(COD, MATURITY + 1)]
    assert close_enough(min(dscr), S["dscr_min"], 1e-12)
    fund = res.records("funding")
    assert close_enough(sum(r["paid"]["up_front"] for r in fund.values()), S["equity_up_front"])
    assert close_enough(next(iter(r["share"] for r in fund.values() if r["share"])), S["debt_share_pro_rata"], 1e-14)
    assert res.failed_checks() == []


def test_six_winter_quarters_capitalise_interest(res):
    pik = {t for t in range(N) if any(p.event == f"senior:pik:{t}" for p in res.group.book("spv").postings)}
    assert pik == {t for t in range(N) if O["debt_repayment"][t] < 0} == {11, 15, 19, 23, 27, 31}


def test_the_sized_cap_binds_only_at_maturity(res):
    # the workbook repays MIN(sizing repayment, opening balance); less was drawn than sized, so the last
    # scheduled repayment is more than is owed
    b = res.group.book("spv")
    owed = b.opening("senior.debt", MATURITY) + b.opening("senior.capitalised_interest", MATURITY)
    assert close_enough(owed, O["debt_repayment"][MATURITY]) and O["sizing_repayment"][MATURITY] > owed
    assert b.balance("senior.debt", MATURITY) == 0


# ── the workbook is one paste short of its own fixed point ───────────────

def test_the_typed_fees_leave_the_sized_debt_partly_undrawn(res):
    drawn = res.group.book("spv").balance("senior.debt", COD - 1)
    assert close_enough(drawn, O["debt_closing"][COD - 1])
    assert float(F(S["debt_sized"]) - drawn) == pytest.approx(2230.11, abs=0.01)
    # the typed IDC and fees are those of a larger debt than the one sized
    assert S["computed_idc"] < I["fixed_uses"]["idc"] and S["computed_up_front_fee"] < I["fixed_uses"]["up_front_fee"]
    assert I["fixed_uses"]["up_front_fee"] / I["up_front_fee_rate"] == pytest.approx(3525191.07, abs=0.01)


@pytest.fixture(scope="module")
def closed():
    case = copy.deepcopy(CASE)
    case["rulebooks"]["funding"]["budget"] = {"loop": "uses_budget", "output": "total"}
    case["loops"]["uses_budget"] = {"rule": {"kind": "uses_total", "record": "funding"}, "start": {"total": 0},
                                    "tolerance": "1e-6", "max_iterations": 12}
    return run_case(case)


def test_closing_the_budget_loop_draws_the_whole_sized_debt(closed):
    rep = closed.loops["uses_budget"]
    assert rep.residual <= 1e-6 and rep.iterations <= 8
    assert rep.values["total"] == pytest.approx(4676798.47, abs=0.01)
    drawn = closed.group.book("spv").balance("senior.debt", COD - 1)
    assert float(drawn) == pytest.approx(S["debt_sized"], abs=1e-3)
    # the converged return, against the workbook's 9.1066%
    assert closed.measure("equity_irr") == pytest.approx(0.091145, abs=1e-6)
    assert closed.failed_checks() == []


# ── refusals ──────────────────────────────────────────────────────────────

def test_a_loop_capped_at_one_run_stops_with_its_residual():
    case = copy.deepcopy(CASE)
    case["loops"]["debt_size"]["max_iterations"] = 1
    with pytest.raises(LoopError, match="loop 'debt_size' residual 3.40968e[+]06"):
        run_case(case)


def test_a_term_that_reads_an_undeclared_loop_refuses():
    case = copy.deepcopy(CASE)
    case["contracts"]["senior"]["terms"]["commitment"] = {"loop": "debt_sise", "output": "debt"}
    with pytest.raises(InputError, match="reads loop 'debt_sise'.*no loop declares"):
        run_case(case)
    del case["loops"]
    with pytest.raises(InputError, match="read a loop, and the case declares none"):
        run_case(case)


def test_a_loop_output_nothing_reads_refuses():
    case = copy.deepcopy(CASE)
    case["loops"]["debt_size"]["start"]["tenor"] = 80
    with pytest.raises(InputError, match="read by nothing: debt_size.tenor"):
        run_case(case)


def test_a_sizing_balance_that_does_not_end_at_zero_refuses():
    # a rate that is not one function of the period: the index and the schedule disagree
    calls = []
    rate = lambda t: calls.append(t) or (F(1, 100) if len(calls) <= 10 else F(2, 100))
    with pytest.raises(SizingError, match="not zero"):
        sculpt({t: F(100) for t in range(10)}, rate, F("1.45"), 0, 9, 9)


def test_negative_cfads_in_a_repayment_period_refuses():
    with pytest.raises(SizingError, match="negative in period 3"):
        sculpt({0: F(10), 1: F(10), 2: F(10), 3: F(-1)}, lambda t: F(1, 100), F("1.45"), 0, 3, 3)
