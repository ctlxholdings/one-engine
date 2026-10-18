"""Valuation pack: the computable core of McKinsey's *Valuation* (5th ed., 2010) on the books.

What this pack is for
---------------------
Ali asked for the book as computable knowledge (2026-10-18), and for its
layers to be considered as a lens over the engine without reshuffling it.
The book's method is to reorganize a company's statements into operating,
nonoperating and financing items, and then read value from a few operating
quantities: NOPLAT, invested capital, free cash flow, ROIC and economic
profit. Both halves live here:

* **Formulas** (this part of the module): the book's definitions and value
  formulas as exact functions over :class:`fractions.Fraction`. Each one
  names the book's equation, chapter and page. The book's derivations
  (Appendices A to D, the ch. 2 derivation of the key value driver formula,
  the ch. 10 continuing-value equivalence) are tests in
  ``tests/test_valuation_formulas.py``, proved in SymPy and fuzzed with
  Hypothesis. A derivation that does not hold is a failed test, never a note.
* **The valuation lens** (:func:`valuation`): the reorganization itself, read
  from a case's books under a classification scheme (one class per account),
  so NOPLAT, invested capital, FCF, ROIC and economic profit come out of any
  case without changing a posting.

Conventions
-----------
* Rates are fractions of one (``Fraction(11, 100)`` is 11%), never percents.
* Periods follow the book: ``t = 0`` is the valuation date, the first cash
  flow falls at ``t = 1`` and is discounted one full period.
* A formula outside its domain refuses (:class:`ValuationError`) instead of
  returning a number the book says is meaningless: a perpetuity whose growth
  is not below the discount rate (ch. 10, p. 230: "Otherwise the formula goes
  to infinity"), a value driver formula with a nil RONIC.

Source and copyright
--------------------
The book is copyrighted; Ali holds an approved copy. This module carries the
formulas, in our own words and code, with page references. It quotes no
prose. Page numbers are the printed ones.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Dict, List, Mapping, Sequence

from one_engine.cuts import CutError, Series
from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.units import at as _at


class ValuationError(ValueError):
    """A valuation formula was asked for a value outside its domain."""


def _f(x):
    """An exact number: a float goes through its decimal text (0.11 is 11/100, not its binary double).

    Anything that is not a plain number (a SymPy symbol) passes through
    unchanged. That is deliberate: the identity tests hand these very functions
    symbols and simplify the result, so the code that runs is the code that is
    proved, not a restatement of it.
    """
    if isinstance(x, float):
        return Fraction(repr(x))
    if isinstance(x, (int, Fraction, str)):
        return Fraction(x)
    return x


def _perpetuity_domain(rate, g, what: str) -> None:
    # a growing perpetuity converges only when the growth is below the discount rate (App. A, p. 792);
    # a symbolic rate (a proof) has no truth value to test, so only numbers are checked
    if isinstance(rate, Fraction) and isinstance(g, Fraction) and g >= rate:
        raise ValuationError(f"{what}: growth {g} is not below the discount rate {rate}; the perpetuity has no value")


# ── chapter 2: the math of value creation (pp. 40-42) ────────────────────────

def free_cash_flow(noplat, net_investment) -> Fraction:
    """FCF = NOPLAT - net investment (ch. 2, p. 40; ch. 7, p. 135)."""
    return _f(noplat) - _f(net_investment)


def net_investment(ic_now, ic_before) -> Fraction:
    """Net investment = invested capital at t+1 less invested capital at t (ch. 2, p. 40)."""
    return _f(ic_now) - _f(ic_before)


def roic(noplat, invested_capital) -> Fraction:
    """ROIC = NOPLAT / invested capital (ch. 2, p. 40). Which invested capital (opening, average) is the
    caller's choice; the lens reads it from the opening balance, as Appendix B requires for EP."""
    ic = _f(invested_capital)
    if ic == 0:
        raise ValuationError("roic: invested capital is nil")
    return _f(noplat) / ic


def investment_rate(net_inv, noplat) -> Fraction:
    """IR = net investment / NOPLAT (ch. 2, p. 40)."""
    n = _f(noplat)
    if n == 0:
        raise ValuationError("investment_rate: NOPLAT is nil")
    return _f(net_inv) / n


def growth_from_investment(roic_new, ir) -> Fraction:
    """g = ROIC x IR (ch. 2, p. 41): growth is the return on new capital times the share reinvested."""
    return _f(roic_new) * _f(ir)


def growing_perpetuity(cash_flow_1, rate, g) -> Fraction:
    """Value = CF(t=1) / (rate - g) (ch. 2, p. 41; App. A, p. 791)."""
    rate, g = _f(rate), _f(g)
    _perpetuity_domain(rate, g, "growing_perpetuity")
    return _f(cash_flow_1) / (rate - g)


def value_driver(noplat_1, g, ronic, wacc) -> Fraction:
    """The key value driver formula: NOPLAT(t=1) x (1 - g/RONIC) / (WACC - g) (ch. 2, p. 42; ch. 10, p. 214).

    The same formula is the book's recommended continuing value (ch. 10, p. 214),
    with NOPLAT of the first year after the explicit forecast and RONIC the
    return on new capital after it.
    """
    g, ronic, wacc = _f(g), _f(ronic), _f(wacc)
    if ronic == 0:
        raise ValuationError("value_driver: RONIC is nil, so no growth can be financed")
    _perpetuity_domain(wacc, g, "value_driver")
    return _f(noplat_1) * (1 - g / ronic) / (wacc - g)


# ── chapter 6 and appendices A, B: economic profit (pp. 113-115, 791-797) ────

def economic_profit(noplat, ic_opening, wacc) -> Fraction:
    """Economic profit = NOPLAT - WACC x invested capital at the start of the period (ch. 6, p. 117).

    Equal to IC x (ROIC - WACC). Appendix B (p. 797) insists on the
    beginning-of-year capital; with closing capital the EP and DCF values part.
    """
    return _f(noplat) - _f(wacc) * _f(ic_opening)


def discounted(flows: Sequence, rate) -> Fraction:
    """Sum of flows[t-1] / (1 + rate)^t for t = 1..n: the first flow is one period away (App. B, p. 795)."""
    r = _f(rate)
    return sum((_f(x) / (1 + r) ** t for t, x in enumerate(flows, start=1)), Fraction(0))


def ep_value_steady(ic_0, ep_1, wacc, g) -> Fraction:
    """V = IC(0) + EP(1) / (WACC - g): Appendix A's end point (p. 792), valid when RONIC equals ROIC."""
    wacc, g = _f(wacc), _f(g)
    _perpetuity_domain(wacc, g, "ep_value_steady")
    return _f(ic_0) + _f(ep_1) / (wacc - g)


def ep_value(ic: Sequence, eps: Sequence, wacc) -> Fraction:
    """Value from economic profit over a finite horizon: IC(0) + PV(EP) - PV(IC(T)) (App. B, p. 796).

    ``ic`` is invested capital at t = 0..T and ``eps`` economic profit at
    t = 1..T. Appendix B sums to infinity, where the last term vanishes; over
    a finite horizon the capital still invested at T has to be taken out, or
    the EP value counts capital the cash flows never return. With it, this
    equals :func:`discounted` of the free cash flows exactly.
    """
    if len(ic) != len(eps) + 1:
        raise ValuationError(f"ep_value: {len(ic)} capital balances for {len(eps)} periods (need one more)")
    w = _f(wacc)
    return _f(ic[0]) + discounted(eps, w) - _f(ic[-1]) / (1 + w) ** len(eps)


# ── chapter 10: continuing value (pp. 213-231) ──────────────────────────────

def cv_economic_profit(ic_t, roic_t, wacc, noplat_t1, g, ronic) -> Fraction:
    """Economic-profit continuing value (ch. 10, pp. 217-218).

    IC(t) x (ROIC(t) - WACC) / WACC, the first year's EP in perpetuity, plus the
    EP that growth adds: NOPLAT(t+1) x (g/RONIC) x (RONIC - WACC) / WACC,
    capitalised at WACC - g. The DCF continuing value equals this plus IC(t)
    when NOPLAT(t+1) = IC(t) x ROIC(t) (ch. 10, p. 218).
    """
    ic_t, roic_t, wacc, g, ronic = map(_f, (ic_t, roic_t, wacc, g, ronic))
    if wacc == 0 or ronic == 0:
        raise ValuationError("cv_economic_profit: WACC and RONIC must not be nil")
    _perpetuity_domain(wacc, g, "cv_economic_profit")
    first = ic_t * (roic_t - wacc) / wacc
    pv_ep2 = _f(noplat_t1) * (g / ronic) * (ronic - wacc) / wacc
    return first + pv_ep2 / (wacc - g)


def cv_two_stage(noplat_t1, wacc, n, g_a, ronic_a, g_b, ronic_b) -> Fraction:
    """Two-stage value driver continuing value (ch. 10, p. 230).

    Stage A grows at ``g_a`` with RONIC ``ronic_a`` for ``n`` years (``g_a``
    may exceed WACC); stage B grows at ``g_b < WACC`` with ``ronic_b`` forever.
    """
    w, g_a, g_b, ronic_a, ronic_b = map(_f, (wacc, g_a, g_b, ronic_a, ronic_b))
    n = int(n)
    _perpetuity_domain(w, g_b, "cv_two_stage (stage B)")
    if ronic_a == 0 or ronic_b == 0:
        raise ValuationError("cv_two_stage: RONIC must not be nil")
    noplat = _f(noplat_t1)
    if g_a == w:
        # the stage-A annuity's limit when growth equals the rate: n level payments of NOPLAT(1 - g/RONIC)/(1+w)
        stage_a = noplat * (1 - g_a / ronic_a) * n / (1 + w)
    else:
        stage_a = noplat * (1 - g_a / ronic_a) / (w - g_a) * (1 - ((1 + g_a) / (1 + w)) ** n)
    stage_b = noplat * (1 + g_a) ** n * (1 - g_b / ronic_b) / ((w - g_b) * (1 + w) ** n)
    return stage_a + stage_b


# ── chapter 11 and appendices C, D: cost of capital (pp. 235-271, 799-812) ───

def wacc(d, e, kd, ke, tm) -> Fraction:
    """WACC = D/(D+E) x kd x (1 - Tm) + E/(D+E) x ke, at market values (ch. 11, p. 236; App. C, p. 802)."""
    d, e = _f(d), _f(e)
    if d + e == 0:
        raise ValuationError("wacc: debt plus equity is nil")
    return d / (d + e) * _f(kd) * (1 - _f(tm)) + e / (d + e) * _f(ke)


def apv(fcf_1, ku, g, tax_shield_1) -> Fraction:
    """Adjusted present value, both legs growing at g: FCF/(ku - g) + tax shield/(ku - g) (App. C, p. 803)."""
    ku, g = _f(ku), _f(g)
    _perpetuity_domain(ku, g, "apv")
    return (_f(fcf_1) + _f(tax_shield_1)) / (ku - g)


def unlevered_ke(d, e, kd, ke) -> Fraction:
    """ku = D/(D+E) x kd + E/(D+E) x ke (App. D, eq. D.3, p. 806): tax shields as risky as operations."""
    d, e = _f(d), _f(e)
    return d / (d + e) * _f(kd) + e / (d + e) * _f(ke)


def unlevered_ke_kd_shields(d, e, kd, ke, v_txa) -> Fraction:
    """ku when tax shields are as risky as debt (App. D, eq. D.4, p. 807): debt net of the shields' value."""
    net = _f(d) - _f(v_txa)
    e = _f(e)
    return net / (net + e) * _f(kd) + e / (net + e) * _f(ke)


def levered_ke(ku, kd, d, e, v_txa=0, k_txa=None) -> Fraction:
    """ke = ku + D/E (ku - kd) - Vtxa/E (ku - ktxa) (App. D, eq. D.6, p. 808).

    With ``k_txa`` left out the shields are as risky as operations (ktxa = ku)
    and the last term drops, which the book recommends (p. 809).
    """
    ku, kd, d, e = map(_f, (ku, kd, d, e))
    k_txa = ku if k_txa is None else _f(k_txa)
    if e == 0:
        raise ValuationError("levered_ke: equity is nil")
    return ku + d / e * (ku - kd) - _f(v_txa) / e * (ku - k_txa)


def levered_beta(beta_u, d, e, beta_d=0) -> Fraction:
    """beta_e = beta_u + D/E (beta_u - beta_d) (App. D, p. 810; ch. 11, eq. 11.1, p. 255); risk-free debt has beta_d = 0."""
    d, e = _f(d), _f(e)
    if e == 0:
        raise ValuationError("levered_beta: equity is nil")
    return _f(beta_u) + d / e * (_f(beta_u) - _f(beta_d))


def unlevered_beta(beta_e, d, e, beta_d=0) -> Fraction:
    """The inverse of :func:`levered_beta`: beta_u = (E beta_e + D beta_d) / (D + E) (App. D, p. 810, solved for beta_u)."""
    d, e = _f(d), _f(e)
    return (e * _f(beta_e) + d * _f(beta_d)) / (d + e)


def capm(rf, beta, mrp) -> Fraction:
    """Cost of equity = risk-free rate + beta x market risk premium, the CAPM (ch. 11, p. 239)."""
    return _f(rf) + _f(beta) * _f(mrp)


# ── the reorganization of the statements (ch. 7, pp. 133-163) ───────────────
#
# The book reorganizes the accountant's statements in three ways (ch. 7, pp.
# 134-137): operating liabilities are netted against operating assets, so
# invested capital is what the operations use; nonoperating assets stand
# apart; and interest, nonoperating income and the taxes on both leave the
# operating profit, so NOPLAT does not depend on how the company is financed.
# A case states that reorganization as a classification scheme: one class of
# CLASSES per account. The lens below reads every quantity from the books
# under it, so no figure is hand-picked; an account the scheme does not
# classify refuses (the completeness invariant of one_engine.cuts).

#: balance-sheet classes on the uses side, read asset-positive (an operating liability is negative)
USES = ("operating_current_asset", "operating_current_liability", "operating_long_term", "goodwill",
        "nonoperating_asset")
#: balance-sheet classes on the sources side, read claim-positive; ``operating_deferred_tax`` is the
#: operating part of the deferred taxes, an equity equivalent whose change turns operating taxes into cash taxes
SOURCES = ("debt", "debt_equivalent", "equity", "equity_equivalent", "operating_deferred_tax")
#: income-statement classes, read income-positive; revenue, operating cost and depreciation make EBITA
PNL = ("revenue", "operating_cost", "depreciation", "amortization", "nonoperating", "interest", "tax")
CLASSES = USES + SOURCES + PNL
_MONEY = (Nature.ASSET, Nature.LIABILITY, Nature.EQUITY, Nature.INCOME, Nature.EXPENSE)


def _series(name: str, values: List[Fraction]) -> Series:
    return Series(name, list(values))


def valuation(group: Group, r: Reader, ctx: Mapping[str, Any]) -> Dict[str, Any]:
    """The book's operating quantities read from one entity's books. Posts nothing.

    Case keys
    ---------
    ``entity``, ``scheme``
        The book and the classification scheme (account -> one of
        ``CLASSES``). Every money account of the book must be classified;
        an account in another unit (a physical stock) is not money and is
        left out.
    ``marginal_tax_rate``
        The rate the book applies to adjusted EBITA for operating taxes, and
        to interest and nonoperating items for their tax effects (ch. 7,
        p. 152, step 2). A number, or a series per period.
    ``other_operating_taxes`` (optional)
        Operating tax items beyond the marginal rate, such as foreign rate
        differences (ch. 7, p. 152, step 3), as a positive tax per period.
    ``ebita_adjustments`` (optional)
        Name -> series added to EBITA, such as the interest inside operating
        lease payments (Exhibit 7.10, "Add: operating lease interest").
    ``roic_on`` (optional)
        ``opening``, ``average`` or ``closing``: the invested capital ROIC
        divides by. Without it no ROIC is reported.
    ``wacc`` (optional)
        For economic profit, charged on the opening invested capital
        (App. B, p. 797). Without it no economic profit is reported.

    Series (each also a cut ``<lens>.<series>``)
    ---------------------------------------------
    Balances at each period's close, uses side asset-positive:
    ``operating_current_assets``, ``operating_current_liabilities`` (negative,
    as the book prints them), ``operating_working_capital``,
    ``operating_long_term``, ``invested_capital_ex_goodwill``, ``goodwill``,
    ``invested_capital``, ``nonoperating_assets``, ``total_funds_invested``;
    sources side claim-positive: ``debt_and_equivalents``,
    ``equity_and_equivalents`` (profit not yet closed to retained earnings
    included), ``total_funds_sources``.

    Flows of each period, income-positive: ``revenue``, ``ebita``,
    ``adjusted_ebita``, ``operating_taxes``, ``operating_cash_taxes`` (operating
    taxes less the increase in operating deferred taxes), ``noplat`` (adjusted
    EBITA less operating cash taxes, as in Exhibit 7.10), ``net_income``,
    ``after_tax_interest`` (the interest expense net of its tax shield,
    positive), ``after_tax_nonoperating`` (nonoperating income and
    amortization net of tax), ``income_to_investors`` (NOPLAT plus
    after-tax nonoperating income), ``depreciation`` (positive),
    ``gross_cash_flow`` (NOPLAT plus depreciation), ``net_investment`` (the
    change in invested capital), ``fcf`` (NOPLAT less net investment),
    ``cash_flow_to_investors`` (FCF plus after-tax nonoperating income less
    the increase in nonoperating assets); and ``roic``,
    ``roic_ex_goodwill``, ``economic_profit`` when asked for.

    Raises
    ------
    CutError
        If an account is not classified, or the scheme uses a class outside
        ``CLASSES`` (a typo would otherwise read as nil).
    """
    entity, scheme_name = r.take("entity"), r.take("scheme")
    book = group.book(entity)
    scheme = ctx["schemes"][scheme_name]
    rate = r.take("marginal_tax_rate")
    other = r.take_or_default("other_operating_taxes", 0, reason="no operating tax beyond the marginal rate").value
    adjustments = r.take_or_default("ebita_adjustments", {}, reason="EBITA is not adjusted").value or {}
    roic_on = r.take_or_default("roic_on", None, reason="no ROIC asked for").value
    wacc_ = r.take_or_default("wacc", None, reason="no economic profit asked for").value
    if roic_on not in (None, "opening", "average", "closing"):
        raise InputError(f"valuation lens: roic_on must be opening, average or closing, not {roic_on!r}")

    accounts = [a for a in book.accounts.values() if a.nature in _MONEY and a.unit == ctx["currency"]]
    missing = sorted(a.name for a in accounts if a.name not in scheme.classes)
    if missing:
        raise CutError(f"scheme '{scheme.name}' does not classify: {', '.join(missing)}")
    unknown = sorted({c for c in scheme.classes.values()} - set(CLASSES))
    if unknown:
        raise CutError(f"scheme '{scheme.name}' uses classes the valuation lens does not know: {', '.join(unknown)}")
    first, n = book.start_period, book.periods - book.start_period

    def total(classes, when) -> List[Fraction]:
        """Per period, the classes' balances: asset-positive for uses and P&L-positive (credit) for the rest."""
        out = [Fraction(0)] * n
        for a in accounts:
            cls = scheme.classes[a.name]
            if cls not in classes:
                continue
            uses = cls in USES
            sign = (1 if a.nature.debit_natured else -1) * (1 if uses else -1)
            for i in range(n):
                t = first + i
                out[i] += sign * (book.opening(a.name, t) if when == "opening" else book.balance(a.name, t))
        return out

    def flow(classes) -> List[Fraction]:
        """Per period, the movement of the P&L classes, income-positive."""
        close, open_ = total(classes, "closing"), total(classes, "opening")
        return [c - o for c, o in zip(close, open_)]

    def add(*xs):
        return [sum(col, Fraction(0)) for col in zip(*xs)]

    def sub(x, y):
        return [a - b for a, b in zip(x, y)]

    def ts(i) -> Fraction:
        return _at(rate, first + i)

    oca, ocl = total(("operating_current_asset",), "closing"), total(("operating_current_liability",), "closing")
    owc = add(oca, ocl)
    olt = total(("operating_long_term",), "closing")
    ic_x = add(owc, olt)
    gw = total(("goodwill",), "closing")
    ic = add(ic_x, gw)
    ic_open = total(("operating_current_asset", "operating_current_liability", "operating_long_term", "goodwill"),
                    "opening")
    ic_x_open = total(("operating_current_asset", "operating_current_liability", "operating_long_term"), "opening")
    nonop = total(("nonoperating_asset",), "closing")
    nonop_open = total(("nonoperating_asset",), "opening")
    funds = add(ic, nonop)
    debt = total(("debt", "debt_equivalent"), "closing")
    # profit not yet closed to retained earnings is equity: the books keep income and expense cumulative
    equity = add(total(("equity", "equity_equivalent", "operating_deferred_tax"), "closing"),
                 total(PNL, "closing"))
    sources = add(debt, equity)

    revenue = flow(("revenue",))
    ebita = flow(("revenue", "operating_cost", "depreciation"))
    adj = add(ebita, *([_at(v, first + i) for i in range(n)] for v in adjustments.values()))
    op_tax = [ts(i) * adj[i] + _at(other, first + i) for i in range(n)]
    dtl = sub(total(("operating_deferred_tax",), "closing"), total(("operating_deferred_tax",), "opening"))
    cash_tax = sub(op_tax, dtl)
    noplat = sub(adj, cash_tax)
    net_income = flow(PNL)
    interest = flow(("interest",))                      # income-positive: an expense is negative
    after_tax_interest = [-x * (1 - ts(i)) for i, x in enumerate(interest)]
    nonop_income = flow(("nonoperating", "amortization"))
    after_tax_nonop = [x * (1 - ts(i)) for i, x in enumerate(nonop_income)]
    depreciation = [-x for x in flow(("depreciation",))]
    net_inv = sub(ic, ic_open)
    fcf = sub(noplat, net_inv)

    series = {
        "operating_current_assets": oca, "operating_current_liabilities": ocl, "operating_working_capital": owc,
        "operating_long_term": olt, "invested_capital_ex_goodwill": ic_x, "goodwill": gw, "invested_capital": ic,
        "nonoperating_assets": nonop, "total_funds_invested": funds, "debt_and_equivalents": debt,
        "equity_and_equivalents": equity, "total_funds_sources": sources,
        "revenue": revenue, "ebita": ebita, "adjusted_ebita": adj, "operating_taxes": op_tax,
        "operating_cash_taxes": cash_tax, "noplat": noplat, "net_income": net_income,
        "after_tax_interest": after_tax_interest, "after_tax_nonoperating": after_tax_nonop,
        "income_to_investors": add(noplat, after_tax_nonop), "depreciation": depreciation,
        "gross_cash_flow": add(noplat, depreciation), "net_investment": net_inv, "fcf": fcf,
        "cash_flow_to_investors": sub(add(fcf, after_tax_nonop), sub(nonop, nonop_open)),
    }
    if roic_on is not None:
        def capital(close, open_):
            if roic_on == "closing":
                return close
            if roic_on == "opening":
                return open_
            return [(a + b) / 2 for a, b in zip(close, open_)]
        series["roic"] = [roic(x, c) for x, c in zip(noplat, capital(ic, ic_open))]
        series["roic_ex_goodwill"] = [roic(x, c) for x, c in zip(noplat, capital(ic_x, ic_x_open))]
    if wacc_ is not None:
        series["economic_profit"] = [economic_profit(x, c, _at(wacc_, first + i))
                                     for i, (x, c) in enumerate(zip(noplat, ic_open))]
    return {"series": {k: _series(k, v) for k, v in series.items()}, "values": {}}


LENSES = {"valuation": valuation}


# ── statements as printed ───────────────────────────────────────────────────

def stated(group: Group, r: Reader, ctx: Mapping[str, Any]):
    """Balances held at the figures a source prints, each change posted against one plug account.

    For a source that prints balance sheets and nothing that moves them (the
    book's invested-capital exhibits): the case states each account's closing
    balance by period, and the books hold it there. Every change is posted
    against ``plug`` (an equity account the rulebook declares). If the printed
    balances balance, the plug never moves; the rulebook's check
    ``<name>: the stated balances balance`` fails at the first period where it
    does, so a misread figure cannot hide in the plug.

    Case keys: ``entity``, ``phase``, ``plug``, and ``balances`` (account ->
    ``{nature: asset | liability | equity, value}``, the value a number or a
    series of closing balances). A balance carried in on the opening balance
    sheet is the starting point.
    """
    entity, phase, plug = r.take("entity"), r.take("phase"), r.take("plug")
    group.declare(entity, plug, Nature.EQUITY, ctx["currency"])
    held = []
    for acct, raw in r.take("balances").items():
        ar = Reader(raw, source=f"stated.balances.{acct}")
        nature, value = ar.take("nature"), ar.take("value")
        ar.finish()
        if nature not in ("asset", "liability", "equity"):
            raise InputError(f"stated.balances.{acct}: nature must be asset, liability or equity, not {nature!r}")
        group.declare(entity, acct, Nature(nature), ctx["currency"])
        held.append((acct, value))

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for acct, value in held:
            change = _at(value, t) - book.balance(acct, t)
            if not change:
                continue
            up = change > 0
            debit_natured = book.accounts[acct].nature.debit_natured
            # raising a debit-natured account debits it; raising a credit-natured one credits it
            debit, credit = (acct, plug) if up == debit_natured else (plug, acct)
            eff = {a: (1 if book.accounts[a].nature.debit_natured == (a == debit) else -1) for a in (debit, credit)}
            g.post(entity, Posting(t, phase, debit, credit, abs(change), eff, "stated", f"{ctx['name']}.{acct}:{t}"))

    group.register(phase, handler)

    def check() -> List[str]:
        book = group.book(entity)
        return [f"period {t}: the plug holds {book.balance(plug, t)}"
                for t in range(book.start_period, book.periods) if book.balance(plug, t)]
    return {f"{ctx['name']}: the stated balances balance": check}


RULEBOOKS = {"stated": stated}
