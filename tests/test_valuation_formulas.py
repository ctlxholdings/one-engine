"""The book's valuation formulas as code, and its derivations as tests (valuation book, 2026-10-18).

:mod:`one_engine.packs.valuation` writes McKinsey *Valuation*'s formulas as
exact functions. This file holds them to the book three ways:

* **the book's own worked numbers** (ch. 10, p. 215: the value driver
  formula, the FCF perpetuity and a 150-year DCF all value the same stream at
  about 1,000);
* **its derivations, proved on the code itself**: the functions accept SymPy
  symbols, so each identity the book derives (ch. 2's key value driver formula,
  Appendix A's economic-profit form, Appendix C's WACC and APV, Appendix D's
  levering equations, ch. 10's DCF and EP continuing values) is the same code
  simplified to zero, not a restatement of it;
* **Hypothesis**, for the identity that holds over any stream rather than a
  steady state: Appendix B's discounted economic profit equals discounted free
  cash flow, here over a finite horizon with the capital still invested at the
  end taken out, on random exact streams.

Each identity has a mutation in ``test_mutations.py`` that turns it red.
"""
from fractions import Fraction as F

import pytest
import sympy as sp
from hypothesis import given, settings, strategies as st

from one_engine.inputs import Reader
from one_engine.packs import valuation as V

ic, noplat, roic, ronic, w, g, ir = sp.symbols("IC NOPLAT ROIC RONIC WACC g IR", positive=True)
d, e, kd, ke, ku, tm, vtxa, ktxa = sp.symbols("D E k_d k_e k_u T_m V_txa k_txa", positive=True)


def zero(expr) -> bool:
    return sp.simplify(expr) == 0


# ── the book's worked example (ch. 10, p. 215) ───────────────────────────────

def test_the_value_driver_formula_values_the_books_stream_at_1000():
    # NOPLAT 100 growing 6%, RONIC 12%, WACC 11%: CV = 100 (1 - 6%/12%) / (11% - 6%) = 1,000
    assert V.value_driver(100, F(6, 100), F(12, 100), F(11, 100)) == 1000


def test_the_fcf_perpetuity_gives_the_same_1000():
    assert V.growing_perpetuity(50, F(11, 100), F(6, 100)) == 1000


def test_discounting_150_years_of_the_stream_gives_the_books_999():
    flows = [50 * F(106, 100) ** (t - 1) for t in range(1, 151)]
    assert round(V.discounted(flows, F(11, 100))) == 999


# ── chapter 2: the key value driver formula (pp. 40-42) ─────────────────────

def test_the_value_driver_formula_is_the_fcf_perpetuity_when_growth_is_roic_times_reinvestment():
    growth = V.growth_from_investment(roic, ir)                       # g = ROIC x IR
    fcf = V.free_cash_flow(noplat, noplat * ir)                       # FCF = NOPLAT (1 - IR)
    assert zero(V.growing_perpetuity(fcf, w, growth) - V.value_driver(noplat, growth, roic, w))


# ── Appendix A: economic profit and the key value driver formula (pp. 791-793) ─

def test_appendix_a_the_value_driver_formula_is_capital_plus_capitalised_economic_profit():
    ep_1 = V.economic_profit(ic * roic, ic, w)                        # IC x (ROIC - WACC)
    assert zero(V.value_driver(ic * roic, g, roic, w) - V.ep_value_steady(ic, ep_1, w, g))


# ── Appendix B: discounted EP equals discounted FCF (pp. 795-797) ────────────

def _streams(ics, noplats):
    fcfs = [V.free_cash_flow(n, V.net_investment(ics[t + 1], ics[t])) for t, n in enumerate(noplats)]
    return fcfs


def test_appendix_b_holds_symbolically_over_three_years():
    ics = sp.symbols("IC0:4", positive=True)
    noplats = sp.symbols("N1:4", positive=True)
    eps = [V.economic_profit(n, ics[t], w) for t, n in enumerate(noplats)]
    assert zero(V.discounted(_streams(ics, noplats), w) - V.ep_value(ics, eps, w))


_money = st.fractions(min_value=-10**6, max_value=10**6, max_denominator=100)


@settings(max_examples=200, deadline=None)
@given(st.integers(min_value=1, max_value=8).flatmap(
    lambda n: st.tuples(st.lists(_money, min_size=n + 1, max_size=n + 1), st.lists(_money, min_size=n, max_size=n))),
    st.fractions(min_value=F(1, 1000), max_value=F(1, 2), max_denominator=1000))
def test_appendix_b_holds_exactly_on_any_stream(stream, rate):
    ics, noplats = stream
    eps = [V.economic_profit(n, ics[t], rate) for t, n in enumerate(noplats)]
    assert V.discounted(_streams(ics, noplats), rate) == V.ep_value(ics, eps, rate)


def test_economic_profit_on_closing_capital_breaks_appendix_b():
    # the book's warning (p. 797): EP must be charged on the beginning-of-year capital
    ics, noplats, rate = [F(100), F(150), F(180)], [F(20), F(25)], F(1, 10)
    wrong = [V.economic_profit(n, ics[t + 1], rate) for t, n in enumerate(noplats)]
    assert V.discounted(_streams(ics, noplats), rate) != V.ep_value(ics, wrong, rate)


def test_ep_value_refuses_capital_that_does_not_cover_every_period():
    with pytest.raises(V.ValuationError, match="need one more"):
        V.ep_value([100, 110], [5, 6], F(1, 10))


# ── chapter 10: continuing value (pp. 214-231) ──────────────────────────────

def test_the_dcf_continuing_value_is_capital_plus_the_economic_profit_continuing_value():
    # ch. 10, p. 218: the DCF CV equals the EP CV plus invested capital, when NOPLAT(t+1) = IC(t) x ROIC(t)
    assert zero(V.value_driver(ic * roic, g, ronic, w) - ic - V.cv_economic_profit(ic, roic, w, ic * roic, g, ronic))


def test_the_two_stage_value_driver_reduces_to_one_stage_when_the_stages_agree():
    n = 7
    assert zero(V.cv_two_stage(noplat, w, n, g, ronic, g, ronic) - V.value_driver(noplat, g, ronic, w))


def test_the_two_stage_formula_is_the_discounted_stage_a_flows_plus_stage_b():
    # stage A: n flows NOPLAT (1+gA)^(t-1) (1 - gA/RONIC_A); stage B: the value driver formula after them
    wacc, ga, ra, gb, rb, n = F(9, 100), F(15, 100), F(20, 100), F(3, 100), F(10, 100), 6
    flows = [100 * (1 + ga) ** (t - 1) * (1 - ga / ra) for t in range(1, n + 1)]
    after = V.value_driver(100 * (1 + ga) ** n, gb, rb, wacc) / (1 + wacc) ** n
    assert V.cv_two_stage(100, wacc, n, ga, ra, gb, rb) == V.discounted(flows, wacc) + after


def test_a_perpetuity_whose_growth_reaches_the_rate_refuses():
    for fn, args in ((V.growing_perpetuity, (50, F(6, 100), F(6, 100))),
                     (V.value_driver, (100, F(12, 100), F(15, 100), F(11, 100))),
                     (V.apv, (100, F(8, 100), F(9, 100), 5))):
        with pytest.raises(V.ValuationError, match="not below the discount rate"):
            fn(*args)


def test_a_value_driver_with_no_return_on_new_capital_refuses():
    with pytest.raises(V.ValuationError, match="RONIC is nil"):
        V.value_driver(100, F(2, 100), 0, F(10, 100))


# ── Appendix C: FCF, WACC and APV agree (pp. 799-803) ───────────────────────

def _steady_state():
    """Debt and equity both growing at g at a constant ratio; equity worth its cash flow capitalised at ke."""
    cfe = e * (ke - g)
    fcf = d * (1 - tm) * kd + cfe - d * g                            # eq. C.1's numerator
    return fcf


def test_appendix_c_free_cash_flow_at_the_wacc_is_debt_plus_equity():
    assert zero(V.growing_perpetuity(_steady_state(), V.wacc(d, e, kd, ke, tm), g) - (d + e))


def test_appendix_c_adjusted_present_value_is_debt_plus_equity():
    k_u = V.unlevered_ke(d, e, kd, ke)                                # eq. D.3: shields as risky as operations
    assert zero(V.apv(_steady_state(), k_u, g, tm * kd * d) - (d + e))


# ── Appendix D: levering and unlevering (pp. 805-811) ───────────────────────

def _d2(k_u, k_e, k_txa):
    """Eq. D.2 with Vu = D - Vtxa + E (eq. D.1): the risk of the assets equals the risk of the claims."""
    vu = d - vtxa + e
    return vu * k_u + vtxa * k_txa - (d * kd + e * k_e)


def test_appendix_d_the_general_levered_cost_of_equity_solves_d2():
    solved = sp.solve(_d2(ku, ke, ktxa), ke)[0]
    assert zero(V.levered_ke(ku, kd, d, e, vtxa, ktxa) - solved)


def test_appendix_d_the_unlevered_cost_with_debt_risk_shields_solves_d2():
    solved = sp.solve(_d2(ku, ke, kd), ku)[0]
    assert zero(V.unlevered_ke_kd_shields(d, e, kd, ke, vtxa) - solved)


def test_appendix_d_levering_undoes_unlevering_when_shields_are_as_risky_as_operations():
    assert zero(V.levered_ke(V.unlevered_ke(d, e, kd, ke), kd, d, e) - ke)


@settings(max_examples=100, deadline=None)
@given(st.fractions(min_value=F(1, 10), max_value=3, max_denominator=100),
       st.fractions(min_value=0, max_value=10**4, max_denominator=10),
       st.fractions(min_value=1, max_value=10**4, max_denominator=10),
       st.fractions(min_value=0, max_value=F(1, 2), max_denominator=100))
def test_appendix_d_levered_and_unlevered_beta_are_inverses(beta_u, debt, equity, beta_d):
    beta_e = V.levered_beta(beta_u, debt, equity, beta_d)
    assert V.unlevered_beta(beta_e, debt, equity, beta_d) == beta_u


def test_the_capm_cost_of_equity():
    # the book's bank example (ch. 36, p. 772): 4.5% + 1.27 x 5.0% = 10.85%
    assert V.capm(F(45, 1000), F(127, 100), F(5, 100)) == F(1085, 10000)


# ── the formulas lent to an exhibit's arithmetic ────────────────────────────

def test_an_exhibits_formula_calls_the_packs_code_beside_an_input_of_the_same_name():
    # the input ``wacc`` is a series, so the value is too: the name read is the input, the name called the code
    r = Reader({"periods": 2, "inputs": {"wacc": ["0.11", "0.10"], "noplat": 100, "g": "0.06", "ronic": "0.12"},
                "values": {"cv": "value_driver(noplat, g, ronic, wacc)"}}, source="test")
    assert V.valuation_formulas(r)["cv"] == [1000, 1250]


def test_an_exhibits_formula_meets_the_packs_refusals():
    r = Reader({"periods": 1, "inputs": {"w": "0.06", "cf": 50, "g": "0.06"},
                "values": {"cv": "growing_perpetuity(cf, w, g)"}}, source="test")
    with pytest.raises(V.ValuationError, match="not below the discount rate"):
        V.valuation_formulas(r)
