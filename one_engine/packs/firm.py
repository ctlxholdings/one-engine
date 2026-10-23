"""Firm pack: an ESCO firm's books, from its deals' dated legs to its statements at each year end.

What this pack is for
---------------------
esco-product reads the firm that carries its deals in two ways, and both are
ported here so its own firm modules can retire:

* **Firm A** (a real firm's printed accounts, Ameresco FY2025): the closing
  balance sheet is derived as the opening one plus the printed movements
  mapped to each line, and compared with the printed closing line by line.
  The ``rollforward`` rulebook does that derivation in a book. Which printed
  row moves which line, and the arithmetic checks on printed figures alone,
  stay with esco-product: they read sources, not books.
* **Firm B** (the model ESCO, EUR): every leg of the six EUR deals in which
  the ESCO pays or is paid becomes a posting under a named posting rule, read
  back as statements at each 31 December. Three ways to build the postings:
  the deals as booked (``base_postings``), each deal under an alternative
  accounting treatment (``template_postings``), and an origination
  simulation that copies the deals year after year around a facility with
  covenants (``simulate``). The ``firm_book`` rulebook posts whichever set it
  is given and records the statements.

Numbers
-------
Everything is an exact Fraction. Amounts are read from the deals' legs as
printed (decimal strings). Rates found by search (``rate``) are bisected on
Fractions and kept to twelve digits, as esco-product does.

Postings
--------
A firm posting is dated and balanced and may have more than two legs (debit
positive, credit negative): a sale of a receivable debits cash and a loss and
credits the receivable. The book takes two-sided postings, so each firm
posting is split into pairs, debits matched against credits in leg order
(``_pairs``). The posting rule travels in the event id
(``<book>.<rule>:<date>#<posting>.<pair>``) so the direct cash flow can be
classified by rule, as esco-product classifies it (``CF_CLASS``).

Accounts come from the firm's chart (passed in: statement, line and normal
side per account). Natures: balance-sheet accounts on an asset line are
assets (contra accounts such as accumulated depreciation included, so their
natural balance is negative), the other balance-sheet accounts liabilities,
equity accounts equity, income-statement accounts income or expense by their
normal side.

Statements (recorded per year by ``firm_book``)
-----------------------------------------------
``balance``: debit-positive balance per account, income and expense closed
into retained earnings together with distributions. ``income``: each income
or expense account's movement in the year on its normal side.
``cash_direct``: cash movements by section (operating, investing,
financing), classified by rule. ``indirect``: net income, then each non-cash
balance-sheet account's change with the cash sign (for investing and
financing accounts only the part posted without cash). ``checks``: assets
equal claims at both ends of the year; opening cash plus cash postings equal
closing cash; retained earnings roll by net income less distributions; the
indirect operating cash equals the direct one.

The simulation
--------------
``simulate`` is esco-product's origination run: each year the settings name
how many copies of each base deal to originate; a copy is its deal's
postings shifted by whole years and recognised with a significant financing
component (revenue at the works date at the present value of the fees,
finance income after). The firm pays overhead at each year end, takes equity,
draws a facility when cash would fall below zero and sweeps it from cash
above zero, and pays interest actual/365 at each year end. A copy is
originated only when cash plus facility headroom covers its first-day need.
At each year end from the first test year, a covenant breach (interest cover
below the minimum, or recourse debt over EBITDA at or above the maximum)
stops new draws; afterwards a copy must also leave the year's reserve, and
cash above the reserve prepays the facility at year ends. Stress runs:
a shortfall template for named deals, refinancing closed from a year,
overhead inflation, clients that stop paying, the breach ignored, a later
first test. Its yearly metrics (covenant EBITDA, interest cover, leverage on
two bases, limits binding) are read from its own postings as it runs,
because the covenant test feeds back into origination.

Not built (refused where reachable): posting rules other than those the
three builders write; a cash posting under a rule with no cash-flow section.
"""
from __future__ import annotations

import bisect
import collections
import datetime as _dt
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine.entities import Group
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting

SOURCE = "firm"

# ── firm A: the derived balance sheet ─────────────────────────────────────────

_SIDES = {"asset": Nature.ASSET, "liability": Nature.LIABILITY, "mezzanine": Nature.LIABILITY, "equity": Nature.EQUITY}


def rollforward(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Opening balance-sheet lines plus their mapped movements, compared with the printed closing lines.

    Inputs: ``entity``, ``phase`` and ``lines``: one per line or group of lines compared as one figure,
    ``{line, side (asset | liability | mezzanine | equity), opening, printed, movements: [{ref, amount}]}``,
    amounts signed as they move the line (positive increases it). The case has one period.

    Each movement posts between its line and ``mapped_movements``, a clearing account. The opening lines
    must balance (assets equal the claims), so the clearing account closes at zero exactly when the mapped
    movements are double-sided overall: that is the check that the derived balance sheet balances.
    Records ``rollforward``: per line the derived closing, the printed closing and the difference (printed
    less derived), and the clearing account's balance.
    """
    name, where = ctx["name"], f"rollforward {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    lines = r.take("lines")
    if group.periods != 1:
        raise InputError(f"{where}: the derivation runs over one period, the case has {group.periods}")
    unit = ctx["currency"]
    seen, net = set(), Fraction(0)
    for x in lines:
        if x["line"] in seen or x["line"] == "mapped_movements":
            raise InputError(f"{where}: line {x['line']!r} given twice or reserved")
        seen.add(x["line"])
        if x["side"] not in _SIDES:
            raise InputError(f"{where}: line {x['line']}: side must be one of {sorted(_SIDES)}, got {x['side']!r}")
        nat = _SIDES[x["side"]]
        group.open(entity, x["line"], nat, unit, Fraction(x["opening"]))
        net += Fraction(x["opening"]) * (1 if nat.debit_natured else -1)
    if net:
        raise InputError(f"{where}: the opening lines do not balance (assets less claims {net})")
    group.declare(entity, "mapped_movements", Nature.EQUITY, unit)

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for x in lines:
            debit_line = book.accounts[x["line"]].nature.debit_natured
            for i, mv in enumerate(x["movements"]):
                a = Fraction(mv["amount"])
                if not a:
                    continue
                # an increase debits an asset line and credits a claim line; the clearing account takes the other side
                up = (a > 0) == debit_line
                dr, cr = (x["line"], "mapped_movements") if up else ("mapped_movements", x["line"])
                eff = {dr: 1 if book.accounts[dr].nature.debit_natured else -1,
                       cr: -1 if book.accounts[cr].nature.debit_natured else 1}
                g.post(entity, Posting(t, phase, dr, cr, abs(a), eff, SOURCE, f"{name}.{x['line']}:{mv['ref']}#{i}"))
        out = {}
        for x in lines:
            derived = book.balance(x["line"], t)
            out[x["line"]] = {"derived": derived, "printed": Fraction(x["printed"]),
                              "difference": Fraction(x["printed"]) - derived}
        g.record(name, t, lines=out, clearing=book.balance("mapped_movements", t))

    group.register(phase, handler)


# ── firm B: postings ──────────────────────────────────────────────────────────

# Cash-flow section of a cash posting, by rule.
CF_CLASS = {
    "T1-fee": "operating", "T2-fee": "operating", "T4-fee": "operating", "T5-fee": "operating", "T6-fee": "operating",
    "T7-fee": "operating", "T8-fee": "operating", "T1-works": "operating", "T3-pay": "operating", "C2": "operating",
    "T9-sale": "operating", "T10-sale": "operating", "W1-release": "operating", "W3-taxpaid": "operating",
    "W4-service": "operating", "W4-staff": "operating", "W4-other": "operating", "C4": "operating",
    "T5-works": "investing",
    "C1": "financing", "C3": "financing", "E1": "financing", "E2": "financing", "F1-fund": "financing", "F1-repay": "financing",
}
# Balance-sheet accounts whose movement is investing or financing in the indirect reconciliation; every other
# non-cash balance-sheet account is an operating adjustment.
INVESTING_ACCOUNTS = {"plant_on_operating_lease"}
FINANCING_ACCOUNTS = {"borrowings", "share_capital", "distributions", "funding_not_printed"}
YEAR_DAYS = 365


@dataclass
class FirmPosting:
    """A dated, balanced firm posting: ``legs`` maps account to amount, debit positive.

    ``due`` marks a progress posting that recognises revenue as a fee falls due (the base recognition);
    the simulation replaces exactly those when it recognises a copy with a financing component.
    """

    date: str
    rule: str
    legs: Dict[str, Fraction]
    deal: str = "firm"
    due: bool = False


class Ledger:
    """The firm's statements read straight from its postings (the simulation reads these as it runs).

    Postings are taken in date order (stable). Every leg must name a chart account and every posting must
    balance. Balances are debit positive.
    """

    def __init__(self, postings: List[FirmPosting], chart: Mapping[str, Mapping[str, str]]):
        self.chart = chart
        self.postings = sorted(postings, key=lambda p: p.date)
        for p in self.postings:
            unknown = set(p.legs) - set(chart)
            if unknown:
                raise InputError(f"firm posting {p.rule} on {p.date} names unknown accounts {sorted(unknown)}")
            if sum(p.legs.values(), Fraction(0)) != 0:
                raise InputError(f"firm posting {p.rule} on {p.date} does not balance")

    def _sum(self, s, t, kinds):
        out: Dict[str, Fraction] = {}
        for p in self.postings:
            if (s is None or p.date > s) and p.date <= t:
                for a, v in p.legs.items():
                    if self.chart[a]["statement"] in kinds:
                        out[a] = out.get(a, Fraction(0)) + Fraction(v)
        return out

    def net_income(self, s, t):
        return -sum(self._sum(s, t, ("income_statement",)).values(), Fraction(0))

    def income_statement(self, s, t):
        return {a: (-v if self.chart[a]["normal_balance"] == "credit" else v)
                for a, v in self._sum(s, t, ("income_statement",)).items()}

    def balance_sheet(self, t):
        bal = self._sum(None, t, ("balance_sheet", "equity"))
        closed = self.net_income(None, t) - bal.pop("distributions", Fraction(0))
        bal["retained_earnings"] = bal.get("retained_earnings", Fraction(0)) - closed
        return bal

    def cash_flow_direct(self, s, t):
        out = {"operating": Fraction(0), "investing": Fraction(0), "financing": Fraction(0)}
        for p in self.postings:
            if (s is None or p.date > s) and p.date <= t and "cash" in p.legs:
                if p.rule not in CF_CLASS:
                    raise InputError(f"cash posting {p.rule} on {p.date} has no cash-flow section")
                out[CF_CLASS[p.rule]] += Fraction(p.legs["cash"])
        return out


def _r2_rows(rows: List[Mapping[str, str]]) -> List[Dict[str, Any]]:
    """A deal's dated legs (esco-product's R2a table: leg, payer, payee, date, amount as printed, or "not
    printed"), each with its line in that table (the header is line 1)."""
    return [dict(x, line=n) for n, x in enumerate(rows, start=2)]


def base_postings(deals: Mapping[str, Any], r2a: Mapping[str, str]) -> Tuple[List[FirmPosting], List[Dict[str, Any]]]:
    """The ESCO's legs of every deal as postings, and the legs not posted (date or amount not printed).

    ``deals``: per deal ``{file, esco, legs: {leg: works | fee_as_due | draw | principal | capex_sold},
    capex_price_legs?, default_draws?: {leg: {sized_to, at}}}``; ``r2a``: file name -> CSV text. Every row
    whose payer or payee is the ESCO posts under its leg's kind:

    * ``works``: T1-works (construction cost paid).
    * ``fee_as_due``: revenue as the fee falls due: T1-progress, T1-billing, T1-fee on the row's date.
    * ``draw`` / ``principal``: C1 / C3.
    * ``capex_sold``: the receivable for the capex price (the sum of the ``capex_price_legs`` rows) is
      recognised (T1-progress, T2-complete) and sold for the row's amount (T9-sale, the difference a loss).

    A ``default_draws`` entry posts a draw the rows do not print, sized to its printed repayment, at the
    date of another leg.
    """
    out: List[FirmPosting] = []
    skipped: List[Dict[str, Any]] = []
    for deal, d in deals.items():
        rows = _r2_rows(r2a[d["file"]])
        esco = d["esco"]
        for x in rows:
            if esco not in x["payer"].split(", ") + x["payee"].split(", "):
                continue
            leg = x["leg"]
            kind = d["legs"].get(leg)
            if x["amount"] == "not printed" or x["date"] == "not printed":
                skipped.append({"deal": deal, "leg": leg, "line": x["line"]})
                continue
            if kind is None:
                raise InputError(f"firm: {deal}: the ESCO's leg {leg} (line {x['line']}) has no kind")
            a, t = Fraction(x["amount"]), x["date"]
            post = lambda rule, legs, due=False: out.append(FirmPosting(t, rule, legs, deal, due))
            if kind == "works":
                post("T1-works", {"cost_of_construction": a, "cash": -a})
            elif kind == "fee_as_due":
                post("T1-progress", {"contract_asset": a, "revenue_construction": -a}, True)
                post("T1-billing", {"trade_receivable": a, "contract_asset": -a})
                post("T1-fee", {"cash": a, "trade_receivable": -a})
            elif kind == "draw":
                post("C1", {"cash": a, "borrowings": -a})
            elif kind == "principal":
                post("C3", {"borrowings": a, "cash": -a})
            elif kind == "capex_sold":
                p = sum((Fraction(y["amount"]) for y in rows
                         if y["leg"] in d["capex_price_legs"] and y["amount"] != "not printed"), Fraction(0))
                post("T1-progress", {"contract_asset": p, "revenue_construction": -p})
                post("T2-complete", {"financing_receivable": p, "contract_asset": -p})
                post("T9-sale", {"cash": a, "loss_on_derecognition": p - a, "financing_receivable": -p})
            else:
                raise InputError(f"firm: {deal}: unknown leg kind {kind!r}")
        for leg, dd in d.get("default_draws", {}).items():
            at = [y for y in rows if y["leg"] == dd["at"] and y["amount"] != "not printed"][0]
            for y in rows:
                if y["leg"] == dd["sized_to"] and y["amount"] != "not printed":
                    a = Fraction(y["amount"])
                    out.append(FirmPosting(at["date"], "C1", {"cash": a, "borrowings": -a}, deal))
    return out, skipped


def fund(ps: List[FirmPosting], scope: str) -> List[FirmPosting]:
    """Where a date's cash postings would leave cash below zero, F1-fund books the shortfall as funding the
    sources do not print, sized to the shortfall, with no interest and no repayment."""
    out, cash = list(ps), Fraction(0)
    for t in sorted({p.date for p in ps}):
        cash += sum((Fraction(p.legs.get("cash", 0)) for p in ps if p.date == t), Fraction(0))
        if cash < 0:
            out.append(FirmPosting(t, "F1-fund", {"cash": -cash, "funding_not_printed": cash}, scope))
            cash = Fraction(0)
    return out


def year_ends(ps: List[FirmPosting]) -> List[str]:
    """31 December of every year from the first posting's to the last's."""
    ys = sorted({int(p.date[:4]) for p in ps})
    return [f"{y}-12-31" for y in range(ys[0], ys[-1] + 1)]


# ── firm B: treatment templates ───────────────────────────────────────────────

TEMPLATES = ("cost_to_cost", "significant_financing", "forfaiting_derecognised", "forfaiting_not_derecognised",
             "finance_lease", "operating_lease", "concession_financial_asset", "concession_intangible", "savings_shortfall")


def _years(a: str, b: str) -> int:
    """Whole years between two dates read by their years (every leg date in these deals is a 1 January)."""
    return int(b[:4]) - int(a[:4])


def rate(price: Fraction, t0: str, pays: List[Tuple[str, Fraction]]) -> Optional[Fraction]:
    """The rate equating ``price`` at ``t0`` to the payments, by bisection on Fractions kept to 1e-12.

    None when every payment is at ``t0`` (no financing to price); 0 when the payments sum to the price.
    """
    if all(_years(t0, d) == 0 for d, _ in pays):
        return None
    if sum(a for _, a in pays) == price:
        return Fraction(0)
    f = lambda x: sum((a / (1 + x) ** _years(t0, d) for d, a in pays), Fraction(0)) - price
    lo, hi = Fraction(-9, 10), Fraction(1)
    for _ in range(80):
        mid = (lo + hi) / 2
        if (f(mid) > 0) == (f(lo) > 0):
            lo = mid
        else:
            hi = mid
    return ((lo + hi) / 2).limit_denominator(10 ** 12)


def deal_inputs(d: Mapping[str, Any], r2a: Mapping[str, str], file: Optional[str] = None) -> Dict[str, Any]:
    """The rows a template reads: works, price legs, the other fees, the sale (``capex_sold``), repayments
    and shortfall payments."""
    rows = [x for x in _r2_rows(r2a[file or d["file"]]) if x["amount"] != "not printed" and x["date"] != "not printed"]
    leg = lambda *ls: [x for x in rows if x["leg"] in ls]
    tc = d.get("templates", {})
    kinds = lambda k: [l for l, v in d["legs"].items() if v == k]
    return {"works": leg(*kinds("works")), "price": leg(*tc.get("price_legs", [])),
            "fees": [x for x in leg(*kinds("fee_as_due")) if x["leg"] not in tc.get("price_legs", [])],
            "sale": leg("c4") if d["legs"].get("c4") == "capex_sold" else [],
            "repay": leg(*kinds("principal")), "shortfall": leg(*tc.get("shortfall_legs", []))}


def template_postings(name: str, deal: str, d: Mapping[str, Any], r2a: Mapping[str, str]):
    """(postings, why not computed, rate) for one deal under one alternative treatment.

    The deal's works cost, price legs and payments run under the treatment named, with no new figures;
    a rate it needs is the one equating the printed price to the printed payments (``rate``).
    """
    if name not in TEMPLATES:
        raise InputError(f"firm: unknown template {name!r}")
    tc = d.get("templates", {})
    if "not_computed" in tc:
        return None, tc["not_computed"], None
    file = tc.get("shortfall_scenario") if name == "savings_shortfall" else None
    if name == "savings_shortfall" and not file:
        return None, "the deal prints no scenario where the ESCO pays a shortfall", None
    x = deal_inputs(d, r2a, file)
    if name in ("forfaiting_derecognised", "forfaiting_not_derecognised", "savings_shortfall") and not x["sale"]:
        return None, "the deal prints no sale of its receivable", None
    t0 = x["works"][0]["date"]
    works = sum((Fraction(w["amount"]) for w in x["works"]), Fraction(0))
    pays = [(p["date"], Fraction(p["amount"])) for p in x["price"]]
    P = sum((a for _, a in pays), Fraction(0))
    term = len({d_ for d_, _ in pays if _years(t0, d_) > 0})
    if name in ("operating_lease", "concession_intangible") and term == 0:
        return None, "the price is paid at the works date, so there is no term to depreciate or amortise over", None
    ps: List[FirmPosting] = []

    def post(t, rule, legs, due=False):
        legs = {k: v for k, v in legs.items() if v}
        if legs:
            ps.append(FirmPosting(t, rule, legs, deal, due))

    def fees_as_due():
        for f in x["fees"]:
            a = Fraction(f["amount"])
            post(f["date"], "T1-progress", {"contract_asset": a, "revenue_construction": -a}, True)
            post(f["date"], "T1-billing", {"trade_receivable": a, "contract_asset": -a})
            post(f["date"], "T1-fee", {"cash": a, "trade_receivable": -a})

    def amortising(asset, accrual_rule, fee_rule, income, r_):
        """An asset at P on t0 paid down by the price payments, accreting at r_, closing exactly at zero."""
        bal, last = P, t0
        for i, (dt, a) in enumerate(pays):
            acc = bal * ((1 + r_) ** _years(last, dt) - 1) if r_ else Fraction(0)
            if i == len(pays) - 1:
                acc = a - bal                     # the last accrual closes the asset; differs only by the rate's rounding
            post(dt, accrual_rule, {asset: acc, income: -acc})
            post(dt, fee_rule, {"cash": a, asset: -a})
            bal, last = bal + acc - a, dt

    r_ = rate(P, t0, pays) if pays else None
    if name in ("forfaiting_derecognised", "forfaiting_not_derecognised", "savings_shortfall"):
        s = x["sale"][0]
        sp = Fraction(s["amount"])
        if name == "savings_shortfall":
            # the forfaiter's rate on the deal's own scenario (its purchase price against the payments it
            # collects) applied to this scenario's payments
            xb = deal_inputs(d, r2a)
            sb = xb["sale"][0]
            fr0 = rate(Fraction(sb["amount"]), sb["date"], [(p["date"], Fraction(p["amount"])) for p in xb["price"]])
            sp = sum((a / (1 + fr0) ** _years(s["date"], dt) for dt, a in pays), Fraction(0))
        post(t0, "T1-works", {"cost_of_construction": works, "cash": -works})
        post(t0, "T1-progress", {"contract_asset": P, "revenue_construction": -P})
        post(t0, "T2-complete", {"financing_receivable": P, "contract_asset": -P})
        for rr in x["repay"]:
            a = Fraction(rr["amount"])
            post(t0, "C1", {"cash": a, "borrowings": -a})
            post(rr["date"], "C3", {"borrowings": a, "cash": -a})
        fees_as_due()
        if name == "forfaiting_not_derecognised":
            fr = rate(sp, s["date"], pays)
            post(s["date"], "T10-sale", {"cash": sp, "receivable_sold_not_derecognised": P, "liability_to_forfaiter": -sp,
                                         "financing_receivable": -P})
            bal, last = sp, s["date"]
            for i, (dt, a) in enumerate(pays):
                acc = bal * ((1 + fr) ** _years(last, dt) - 1)
                if i == len(pays) - 1:
                    acc = a - bal
                # the year's accretion is booked on the 31 December before the collection
                post(f"{int(dt[:4]) - 1}-12-31", "T10-accrual", {"interest_expense": acc, "liability_to_forfaiter": -acc})
                post(dt, "T10-collection", {"liability_to_forfaiter": a, "receivable_sold_not_derecognised": -a})
                bal, last = bal + acc - a, dt
            return ps, None, fr
        post(s["date"], "T9-sale", {"cash": sp, "loss_on_derecognition": P - sp, "financing_receivable": -P})
        if name == "savings_shortfall":
            tot = sum((Fraction(y["amount"]) for y in x["shortfall"]), Fraction(0))
            post(t0, "T3-expect", {"revenue_construction": tot, "refund_liability_shortfall": -tot})
            for y in x["shortfall"]:
                a = Fraction(y["amount"])
                post(y["date"], "T3-pay", {"refund_liability_shortfall": a, "cash": -a})
        return ps, None, None

    post(t0, "T5-works" if name == "operating_lease" else "T1-works",
         {("plant_on_operating_lease" if name == "operating_lease" else "cost_of_construction"): works, "cash": -works})
    fees_as_due()
    if name == "cost_to_cost":
        post(t0, "T1-progress", {"contract_asset": P, "revenue_construction": -P})
        for dt, a in pays:
            post(dt, "T1-billing", {"trade_receivable": a, "contract_asset": -a})
            post(dt, "T1-fee", {"cash": a, "trade_receivable": -a})
    elif name == "significant_financing":
        post(t0, "T1-progress", {"contract_asset": P, "revenue_construction": -P})
        post(t0, "T2-complete", {"financing_receivable": P, "contract_asset": -P})
        amortising("financing_receivable", "T2-accrual", "T2-fee", "finance_income_contract", r_)
    elif name == "finance_lease":
        post(t0, "T4-commence", {"lease_receivable": P, "revenue_lease_selling": -P})
        amortising("lease_receivable", "T4-accrual", "T4-fee", "finance_income_lease", r_)
    elif name == "concession_financial_asset":
        post(t0, "T6-progress", {"concession_financial_asset": P, "revenue_construction": -P})
        amortising("concession_financial_asset", "T6-accrual", "T6-fee", "finance_income_contract", r_)
    elif name == "operating_lease":
        for dt, a in pays:
            post(dt, "T5-fee", {"cash": a, "revenue_operating_lease": -a})
        for k in range(term):
            post(f"{int(t0[:4]) + k}-12-31", "T5-depreciation", {"depreciation": works / term, "accumulated_depreciation": -works / term})
    elif name == "concession_intangible":
        post(t0, "T7-progress", {"concession_intangible": P, "revenue_construction": -P})
        for dt, a in pays:
            post(dt, "T7-fee", {"cash": a, "revenue_service": -a})
        for k in range(term):
            post(f"{int(t0[:4]) + k}-12-31", "T7-amortisation", {"amortisation": P / term, "accumulated_amortisation": -P / term})
    return ps, None, r_


# ── firm B: origination simulation ────────────────────────────────────────────

ICOV = "debt service coverage (interest cover)"
ALL = "TFD/EBITDA (all debt)"
REC = "TFD/EBITDA (recourse debt)"
STOP = "origination stopped: no funding"
STOP_COV = "origination stopped: covenant breached"
POST_BREACH = ("after a breach a new copy is originated only if the cash left after it still covers the year's reserve plus "
               "its own up-front overhead: our default; the agreement's terms are not printed")
DRAW_STOP = "new facility draws stopped after a covenant breach (our default; the agreement's terms are not printed)"
SHORT = "cash short (not funded)"
STRESSES = ("shortfall_paid", "refinancing_not_available", "om_cost_inflation", "client_default", "breach_ignored",
            "first_test_year3")
EBITDA_ADD = ("interest_expense", "loss_on_derecognition", "depreciation", "amortisation", "income_tax_expense")
EBITDA_SUB = ("finance_income_lease", "finance_income_contract")
BRIDGE = {  # equity bridge line: the income-statement accounts it sums
    "margin (revenue less cost of construction and of shortfalls)": ("revenue_construction", "revenue_service",
                                                                     "revenue_operating_lease", "revenue_lease_selling",
                                                                     "cost_of_construction", "cost_of_service", "shortfall_expense",
                                                                     "depreciation", "amortisation"),
    "overhead": ("other_operating_expense", "staff_cost"),
    "net interest": ("interest_expense",),
    "loss on sale of receivables": ("loss_on_derecognition",),
    "write-offs": ("credit_loss_expense",),
    "finance income": ("finance_income_contract", "finance_income_lease"),
    "grant and tax": ("grant_income", "income_tax_expense"),
}


def _d(t: str) -> _dt.date:
    return _dt.date.fromisoformat(t)


def _num(x) -> Fraction:
    return Fraction(str(x))


def fees_pv(tp: List[FirmPosting], t0: str, r: Fraction) -> Fraction:
    """Present value at t0 of a deal's fee cash (T1-fee), discounted at r over whole years."""
    return sum((p.legs["cash"] / (1 + r) ** _years(t0, p.date) for p in tp if p.rule == "T1-fee"), Fraction(0))


def to_financing(tp: List[FirmPosting], d: str, r: Fraction) -> List[FirmPosting]:
    """A copy's fees recognised at the works date at their present value at r (T1-progress, T2-complete),
    then T2-accrual and T2-fee on each fee date; the receivable closes exactly at the last fee."""
    fees = sorted((p for p in tp if p.rule == "T1-fee"), key=lambda p: p.date)
    if not fees:
        return tp
    keep = [p for p in tp if p.rule not in ("T1-billing", "T1-fee") and not (p.rule == "T1-progress" and p.due)]
    works = [p for p in keep if p.rule in ("T1-works", "T5-works")]
    tw = min(p.date for p in works) if works else min(p.date for p in tp)
    pv = sum((p.legs["cash"] / (1 + r) ** _years(tw, p.date) for p in fees), Fraction(0))
    out = keep + [FirmPosting(tw, "T1-progress", {"contract_asset": pv, "revenue_construction": -pv}, d),
                  FirmPosting(tw, "T2-complete", {"financing_receivable": pv, "contract_asset": -pv}, d)]
    bal, last = pv, tw
    for i, p in enumerate(fees):
        a = p.legs["cash"]
        acc = a - bal if i == len(fees) - 1 else bal * ((1 + r) ** _years(last, p.date) - 1)
        if acc:
            out.append(FirmPosting(p.date, "T2-accrual", {"financing_receivable": acc, "finance_income_contract": -acc}, d))
        out.append(FirmPosting(p.date, "T2-fee", {"cash": a, "financing_receivable": -a}, d))
        bal, last = bal + acc - a, p.date
    return out


def financing_rate(d: Mapping[str, Any], r2a: Mapping[str, str], r: Fraction) -> Fraction:
    """The financing component's rate: a deal that sells its receivable (``capex_sold``) uses the buyer's
    rate (purchase price against the payments it collects); every other deal the firm's debt rate."""
    if d["legs"].get("c4") == "capex_sold":
        x = deal_inputs(d, r2a)
        s = x["sale"][0]
        return rate(Fraction(s["amount"]), s["date"], [(p["date"], Fraction(p["amount"])) for p in x["price"]])
    return r


def copy_templates(deals, r2a, s, stress=None) -> Dict[str, List[FirmPosting]]:
    """Each base deal's postings as the copy template, recognised with a financing component.

    A template with no works cost gets one on its first date: the present value of its fees at the debt
    rate times (1 - ``works_cost_margin``). Under ``shortfall_paid`` the deals it names take that template.
    """
    ps, _ = base_postings(deals, r2a)
    out = {d: [p for p in ps if p.deal == d] for d in deals}
    mg, r = _num(s["works_cost_margin"]), _num(s["debt_rate"])
    for d, tp in out.items():
        if tp and not any(p.rule in ("T1-works", "T5-works") for p in tp):
            t0 = min(p.date for p in tp)
            c = fees_pv(tp, t0, r) * (1 - mg)
            tp.insert(0, FirmPosting(t0, "T1-works", {"cost_of_construction": c, "cash": -c}, d))
    if stress == "shortfall_paid":
        for d, t in s["stress"]["shortfall_paid"].items():
            tp, why, _ = template_postings(t, d, deals[d], r2a)
            if tp is None:
                raise InputError(f"firm: {t} is not computed for {d}: {why}")
            out[d] = tp
    return {d: to_financing(tp, d, financing_rate(deals[d], r2a, r)) for d, tp in out.items()}


def _shift(p: FirmPosting, years: int, deal_id: str) -> FirmPosting:
    return FirmPosting(f"{int(p.date[:4]) + years}{p.date[4:]}", p.rule, dict(p.legs), deal_id, p.due)


def candidates(deals, r2a, s, stress=None) -> List[Tuple[str, str, List[FirmPosting]]]:
    """Every copy the settings would originate, in order: (first date, copy id, postings)."""
    tpl = copy_templates(deals, r2a, s, stress)
    y0, n = s["start_year"], s["origination_years"]
    out = []
    for y in range(y0, y0 + n):
        for deal, k in s["projects_per_year"].items():
            src = tpl[deal]
            if not k or not src:
                continue
            first = min(int(p.date[:4]) for p in src)
            for i in range(k):
                pid = f"{deal}@{y}#{i + 1}"
                cp = [_shift(p, y - first, pid) for p in src]
                cd = s["stress"]["client_default"] if stress == "client_default" else None
                if cd and deal in cd["deals"]:
                    cut = y + cd["after_years"]
                    gone = [p for p in cp if int(p.date[:4]) >= cut and p.rule in ("T2-accrual", "T2-fee")]
                    cp = [p for p in cp if p not in gone]
                    left = sum((p.legs.get("financing_receivable", 0) for p in cp), Fraction(0))
                    if left:
                        t = f"{cut}-01-01"
                        cp += [FirmPosting(t, "W2-ecl", {"credit_loss_expense": left, "loss_allowance": -left}, pid),
                               FirmPosting(t, "W2-writeoff-financing", {"loss_allowance": left, "financing_receivable": -left}, pid)]
                out.append((min(p.date for p in cp), pid, cp))
    return out


def cash_available(yr: List[FirmPosting], op: Fraction):
    """(cash available for debt service, interest paid, scheduled principal due, repayments at maturity).

    Cash available is the year's operating cash with works paid in cash and interest paid taken back out
    (interest is debt service, not operating cost). No copy prints an amortising schedule, so scheduled
    principal is nil and repayments count as maturities, outside the ratio.
    """
    works = -sum((p.legs["cash"] for p in yr if p.rule in ("T1-works", "T5-works")), Fraction(0))
    int_paid = -sum((p.legs["cash"] for p in yr if p.rule == "C2"), Fraction(0))
    maturity = -sum((p.legs["cash"] for p in yr if p.rule in ("C3", "F1-repay")), Fraction(0))
    return op + works + int_paid, int_paid, Fraction(0), maturity


def metrics(ps, s, chart, y0, last, staff, funded, only_last=False) -> List[Dict[str, Any]]:
    """Per year: cash, equity, debt, revenue, covenant EBITDA, interest cover, leverage, and the limits binding.

    Covenant EBITDA is net income plus interest, the loss on sale of receivables, depreciation,
    amortisation and tax, less finance income, plus the contract finance income accrued (T2-accrual).
    Debt is every borrowing plus funding not printed; recourse debt only the firm's own facility.
    """
    bk = Ledger(ps, chart)
    cov = s["covenants"]
    rows, prev = [], None
    for y in range(y0, last + 1):
        t = f"{y}-12-31"
        if only_last and y != last:
            prev = t
            continue
        b = bk.balance_sheet(t)
        inc = bk.income_statement(prev, t)
        ni = bk.net_income(prev, t)
        rev = sum((v for a, v in inc.items() if chart[a]["line"] == "revenue"), Fraction(0))
        yr = [p for p in bk.postings if (prev is None or p.date > prev) and p.date <= t]
        t2 = -sum((p.legs.get("finance_income_contract", 0) for p in yr if p.rule == "T2-accrual"), Fraction(0))
        ebitda = (ni + sum((inc.get(a, Fraction(0)) for a in EBITDA_ADD), Fraction(0))
                  - sum((inc.get(a, Fraction(0)) for a in EBITDA_SUB), Fraction(0)) + t2)
        interest = inc.get("interest_expense", Fraction(0))
        debt = -b.get("borrowings", Fraction(0)) - b.get("funding_not_printed", Fraction(0))
        rdebt = -sum((p.legs.get("borrowings", 0) for p in bk.postings if p.date <= t and p.deal == "firm"), Fraction(0))
        equity = -(b.get("share_capital", Fraction(0)) + b.get("retained_earnings", Fraction(0)))
        cav, int_paid, due, maturity = cash_available(yr, bk.cash_flow_direct(prev, t)["operating"])
        service = int_paid + due
        dscr = (cav / service) if service else None
        lev = (debt / ebitda) if debt and ebitda > 0 else None
        rlev = (rdebt / ebitda) if rdebt and ebitda > 0 else None
        binds = []
        if funded["stopped"].get(y):
            binds.append(f"{STOP} ({funded['stopped'][y]} copies not originated)")
        if funded["short"].get(y):
            binds.append(SHORT)
        if funded["stopped_cov"].get(y):
            binds.append(f"{STOP_COV} ({funded['stopped_cov'][y]} copies not originated; {POST_BREACH})")
        if funded.get("breach_year") == y:
            binds.append(DRAW_STOP)
        if service and dscr < cov["dscr_min"]:
            binds.append(f"{ICOV} below {cov['dscr_min']}")
        if debt and (ebitda <= 0 or lev >= cov["tfd_ebitda_max"]):
            binds.append(f"{ALL} at or above {cov['tfd_ebitda_max']}")
        if rdebt and (ebitda <= 0 or rlev >= cov["tfd_ebitda_max"]):
            binds.append(f"{REC} at or above {cov['tfd_ebitda_max']}")
        if equity < 0:
            binds.append("equity below zero")
        rows.append({"year": y, "cash": b.get("cash", Fraction(0)), "equity": equity, "debt": debt,
                     "funding_not_printed": -b.get("funding_not_printed", Fraction(0)), "revenue": rev, "ebitda": ebitda,
                     "net_income": ni, "interest": interest, "debt_service": service, "cash_available": cav,
                     "maturities": maturity, "dscr": dscr, "tfd_ebitda": lev, "recourse_debt": rdebt, "tfd_ebitda_recourse": rlev,
                     "binds": binds, "active_projects": staff[y][0], "staff": staff[y][1],
                     "cash_short": funded["short"].get(y, Fraction(0)), "not_originated": funded["stopped"].get(y, 0),
                     "not_originated_cov": funded["stopped_cov"].get(y, 0)})
        prev = t
    return rows


def simulate(deals, r2a, s, chart, stress=None):
    """One origination run: (postings, yearly metrics, staff by year); see the module docstring.

    ``s``: the settings with every rate and ratio numeric: ``start_year``, ``origination_years``,
    ``projects_per_year`` (deal -> copies a year), ``equity_paid_in``, ``debt_available``, ``debt_rate``,
    ``overhead`` (share of the year's revenue), ``fixed_cost`` (a year, optional), ``works_cost_margin``,
    ``draw_stop``, ``covenants: {dscr_min, tfd_ebitda_max}``, ``staff_per_project``, ``fixed_staff`` and
    ``stress`` (the parameters of each stress run). Staff carry no cost.
    """
    if stress is not None and stress not in STRESSES:
        raise InputError(f"firm: unknown stress {stress!r}")
    y0 = s["start_year"]
    cands = candidates(deals, r2a, s, stress)
    infl = _num(s["stress"]["om_cost_inflation"]) if stress == "om_cost_inflation" else Fraction(0)
    refi = s["stress"]["refinancing_not_available_from"] if stress == "refinancing_not_available" else None
    rate_, limit = _num(s["debt_rate"]), Fraction(s["debt_available"])
    ratio, fixed = _num(s["overhead"]), _num(s.get("fixed_cost", 0))
    queue: Dict[str, List[FirmPosting]] = {}
    if s["equity_paid_in"]:
        e = Fraction(s["equity_paid_in"])
        queue.setdefault(f"{y0}-01-01", []).append(FirmPosting(f"{y0}-01-01", "E1", {"cash": e, "share_capital": -e}))
    starts: Dict[str, list] = {}
    for c in cands:
        starts.setdefault(c[0], []).append(c)
    out: List[FirmPosting] = []
    cash, fac = Fraction(0), Fraction(0)
    accrued, last_t = Fraction(0), None
    active, stopped, short, stopped_cov = {}, {}, {}, {}
    frozen, breach_year = False, None
    reserve: Dict[int, Fraction] = {}
    draw_stop = s.get("draw_stop", True) and stress != "breach_ignored"
    first_test = y0 + (2 if stress == "first_test_year3" else 0)
    last = y0 + s["origination_years"] - 1
    done = set()
    closed = lambda t: (refi and int(t[:4]) >= refi) or frozen
    while True:
        ends = {f"{y}-12-31" for y in range(y0, last + 1)}
        todo = sorted((set(queue) | set(starts) | ends) - done)
        if not todo:
            break
        t = todo[0]
        done.add(t)
        if last_t is not None and fac:
            # interest actual/365 on the facility between dated events, paid at the year end
            accrued += fac * rate_ * Fraction((_d(t) - _d(last_t)).days, YEAR_DAYS)
        last_t = t
        day = queue.pop(t, [])
        out += day
        cash += sum((Fraction(p.legs.get("cash", 0)) for p in day), Fraction(0))
        open_ = max(limit - fac, Fraction(0))
        headroom = Fraction(0) if closed(t) else open_
        for t0, pid, cp in starts.pop(t, []):
            need = -sum((Fraction(p.legs.get("cash", 0)) for p in cp if p.date == t0), Fraction(0))
            gate = need
            if frozen:      # after a breach a copy must also leave the year's reserve and its own overhead
                rev_y = -sum((v for p in cp if p.date[:4] == t[:4] for a_, v in p.legs.items() if a_.startswith("revenue_")),
                             Fraction(0))
                gate += reserve.get(int(t[:4]) - 1, Fraction(0)) + ratio * rev_y * (1 + infl) ** (int(t[:4]) - y0)
            if gate > cash + headroom:
                if frozen and gate <= cash + open_:       # the facility would fund it but draws are stopped
                    stopped_cov[int(t[:4])] = stopped_cov.get(int(t[:4]), 0) + 1
                else:
                    stopped[int(t[:4])] = stopped.get(int(t[:4]), 0) + 1
                continue
            for p in cp:
                (out if p.date == t else queue.setdefault(p.date, [])).append(p)
            cash -= need
            yrs = [int(p.date[:4]) for p in cp]
            active[pid] = (min(yrs), max(yrs))
            last = max(last, max(yrs))
            if cash < 0:                                  # the copy's own need, within the headroom just checked
                dr = min(-cash, headroom)
                out.append(FirmPosting(t, "C1", {"cash": dr, "borrowings": -dr}))
                fac, cash, headroom = fac + dr, cash + dr, headroom - dr
        if t.endswith("-12-31"):
            y = int(t[:4])
            rev = -sum((v for p in out if p.date[:4] == str(y) for a, v in p.legs.items() if a.startswith("revenue_")), Fraction(0))
            o = (ratio * rev + fixed) * (1 + infl) ** (y - y0)
            if o:
                out.append(FirmPosting(t, "W4-other", {"other_operating_expense": o, "cash": -o}))
                cash -= o
            if accrued:
                out.append(FirmPosting(t, "C2", {"interest_expense": accrued, "cash": -accrued}))
                cash -= accrued
                accrued = Fraction(0)
        if cash < 0:
            dr = min(-cash, Fraction(0) if closed(t) else max(limit - fac, Fraction(0)))
            if dr:
                out.append(FirmPosting(t, "C1", {"cash": dr, "borrowings": -dr}))
                fac, cash = fac + dr, cash + dr
        elif cash > 0 and fac > 0 and not frozen:         # before a breach: sweep, with redraw
            rp = min(cash, fac)
            out.append(FirmPosting(t, "C3", {"borrowings": rp, "cash": -rp}))
            fac, cash = fac - rp, cash - rp
        if t.endswith("-12-31") and frozen and fac > 0 and cash > 0:   # after a breach: prepay on 31 December, last
            y = int(t[:4])
            nxt = -sum((v for d_, ps_ in queue.items() if d_[:4] == str(y + 1) for p in ps_ for a, v in p.legs.items()
                        if a.startswith("revenue_")), Fraction(0))
            ov = (ratio * nxt + fixed) * (1 + infl) ** (y + 1 - y0)
            # reserve = next year's overhead + a year's interest on the balance left after the prepayment
            yr_rate = rate_ * Fraction((_d(f"{y + 2}-01-01") - _d(f"{y + 1}-01-01")).days, YEAR_DAYS)
            rp = min(fac, max((cash - ov - fac * yr_rate) / (1 - yr_rate), Fraction(0)))
            reserve[y] = ov + (fac - rp) * yr_rate
            if rp:
                out.append(FirmPosting(t, "C3", {"borrowings": rp, "cash": -rp}))
                fac, cash = fac - rp, cash - rp
        if t.endswith("-12-31"):
            if cash < 0:
                short[int(t[:4])] = -cash
            if draw_stop and not frozen and int(t[:4]) >= first_test:   # a breach at a year end stops new draws
                r = metrics(out, s, chart, y0, int(t[:4]), collections.defaultdict(lambda: (0, 0)),
                            {"stopped": {}, "short": {}, "stopped_cov": {}}, only_last=True)[-1]
                if any(b.startswith((ICOV, REC)) for b in r["binds"]):
                    frozen, breach_year = True, int(t[:4])
    staff = {}
    for y in range(y0, last + 1):
        nproj = sum(1 for a, b in active.values() if a <= y <= b)
        staff[y] = (nproj, s["fixed_staff"] + s["staff_per_project"] * nproj)
    rows = metrics(out, s, chart, y0, last, staff, {"stopped": stopped, "short": short, "stopped_cov": stopped_cov,
                                                    "breach_year": breach_year})
    for r in rows:
        r["reserve"] = reserve.get(r["year"])
    return out, rows, staff


def equity_bridge(ps: List[FirmPosting], chart) -> Dict[str, Fraction]:
    """Paid-in equity plus each income-statement group equals closing equity, at the last date, exactly.

    Every income-statement account the postings use must fall in a ``BRIDGE`` group.
    """
    bk = Ledger(ps, chart)
    t = max(p.date for p in ps)
    tot: Dict[str, Fraction] = {}
    for p in bk.postings:
        for a, v in p.legs.items():
            tot[a] = tot.get(a, Fraction(0)) + Fraction(v)
    out = {"equity paid in": -tot.get("share_capital", Fraction(0))}
    used = {"share_capital"}
    for line, accs in BRIDGE.items():
        out[line] = -sum((tot.get(a, Fraction(0)) for a in accs), Fraction(0))
        used |= set(accs)
    left = [a for a in tot if chart[a]["statement"] == "income_statement" and a not in used and tot[a]]
    if left:
        raise InputError(f"firm: income-statement accounts not in the equity bridge: {left}")
    b = bk.balance_sheet(t)
    out["closing equity"] = -(b.get("share_capital", Fraction(0)) + b.get("retained_earnings", Fraction(0)))
    return out


def first_bind(rows, basis="all"):
    """The first year a limit binds and which, on one leverage basis ("all" ignores the recourse ratio,
    "recourse" the all-debt one)."""
    skip = REC if basis == "all" else ALL
    for r in rows:
        b = [x for x in r["binds"] if not x.startswith(skip)]
        if b:
            return r["year"], "; ".join(b)
    return None, "no limit binds"


# ── firm B: the book ──────────────────────────────────────────────────────────

def _nature(acct: Mapping[str, str]) -> Nature:
    if acct["statement"] == "income_statement":
        return Nature.EXPENSE if acct["normal_balance"] == "debit" else Nature.INCOME
    if acct["statement"] == "equity":
        return Nature.EQUITY
    return Nature.ASSET if "assets" in acct["line"] else Nature.LIABILITY


def _pairs(legs: Mapping[str, Fraction]) -> List[Tuple[str, str, Fraction]]:
    """A balanced multi-leg posting as two-sided ones: debits matched against credits in leg order."""
    dr = [[a, v] for a, v in legs.items() if v > 0]
    cr = [[a, -v] for a, v in legs.items() if v < 0]
    out, i, j = [], 0, 0
    while i < len(dr) and j < len(cr):
        x = min(dr[i][1], cr[j][1])
        out.append((dr[i][0], cr[j][0], x))
        dr[i][1] -= x
        cr[j][1] -= x
        i += dr[i][1] == 0
        j += cr[j][1] == 0
    return out


def firm_book(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """The ESCO firm's postings in one book, with its statements recorded per year; see the module docstring.

    Inputs: ``entity``, ``phase``, ``chart`` (account -> ``{statement, line, normal_balance}``),
    ``deals`` (per deal: file, ESCO party, leg kinds, template and draw settings), ``r2a`` (file -> the
    deal's dated legs, ``{leg, payer, payee, date, amount}`` each), ``period_ends`` (31 December of each year; a posting books in the first period
    ending on or after its date) and ``book``, one of:

    * ``{kind: base}``: every deal (``deal`` names one to book it alone), unfunded cash gaps funded (F1-fund);
    * ``{kind: template, template, deal}``: one deal under one treatment, gaps funded;
    * ``{kind: simulation, settings, stress?}``: the origination run (``simulate``), not funded.

    Records ``firm`` per period: ``balance``, ``income``, ``cash_direct``, ``indirect``, ``checks`` and
    ``working_capital``; a simulation also ``metrics`` (its row for the year) and, in the last period,
    ``bridge``. A template that cannot run records ``not_computed`` and posts nothing.
    """
    name, where = ctx["name"], f"firm_book {ctx['name']}"
    entity, phase = r.take("entity"), r.take("phase")
    chart, deals, r2a = r.take("chart"), r.take("deals"), r.take("r2a")
    ends = list(r.take("period_ends"))
    spec = r.take("book")
    if len(ends) != group.periods or ends != sorted(set(ends)):
        raise InputError(f"{where}: period_ends must be {group.periods} ascending dates, one per period")
    kind, extra = spec.get("kind"), {}
    if kind == "base":
        ps, skipped = base_postings(deals, r2a)
        if spec.get("deal"):
            ps = [p for p in ps if p.deal == spec["deal"]]
        ps = fund(ps, spec.get("deal") or name)
        extra["not_posted"] = skipped
    elif kind == "template":
        tps, why, rt = template_postings(spec["template"], spec["deal"], deals[spec["deal"]], r2a)
        if tps is None:
            ps, extra["not_computed"] = [], why
        else:
            ps, extra["rate"] = fund(tps, f"{spec['template']}|{spec['deal']}"), rt
    elif kind == "simulation":
        ps, rows, staff = simulate(deals, r2a, spec["settings"], chart, spec.get("stress"))
        extra["metrics"] = {r_["year"]: r_ for r_ in rows}
        extra["bridge"] = equity_bridge(ps, chart)
    else:
        raise InputError(f"{where}: book kind must be base, template or simulation, got {kind!r}")
    Ledger(ps, chart)                                     # every leg on a chart account, every posting balanced
    ps = sorted(ps, key=lambda p: p.date)
    if ps and ps[-1].date > ends[-1]:
        raise InputError(f"{where}: a posting on {ps[-1].date} falls after the last period end {ends[-1]}")
    unit = ctx["currency"]
    for a, acct in chart.items():
        group.declare(entity, a, _nature(acct), unit)
    by_period: Dict[int, List[Tuple[int, FirmPosting]]] = {}
    for i, p in enumerate(ps):
        if any(v and "cash" == a for a, v in p.legs.items()) and p.rule not in CF_CLASS:
            raise InputError(f"{where}: cash posting {p.rule} on {p.date} has no cash-flow section")
        by_period.setdefault(bisect.bisect_left(ends, p.date), []).append((i, p))
    bs_accounts = [a for a, x in chart.items() if x["statement"] in ("balance_sheet", "equity")]
    asset_ids = {a for a, x in chart.items() if x["statement"] == "balance_sheet"
                 and x["line"].startswith(("current assets", "non-current assets"))}

    def debit_positive(book, a, t):
        v = book.balance(a, t) if t >= 0 else Fraction(0)
        return v if book.accounts[a].nature.debit_natured else -v

    def sheet(book, t):
        """Debit-positive balances at the end of period t (t = -1: before the first), income closed into
        retained earnings with distributions."""
        bal = {a: debit_positive(book, a, t) for a in bs_accounts}
        ni = -sum((debit_positive(book, a, t) for a, x in chart.items() if x["statement"] == "income_statement"), Fraction(0))
        bal["retained_earnings"] -= ni - bal.pop("distributions")
        return bal, ni

    def handler(g: Group, t: int) -> None:
        book = g.book(entity)
        for i, p in by_period.get(t, []):
            for j, (dr, cr, x) in enumerate(_pairs(p.legs)):
                eff = {dr: 1 if book.accounts[dr].nature.debit_natured else -1,
                       cr: -1 if book.accounts[cr].nature.debit_natured else 1}
                g.post(entity, Posting(t, phase, dr, cr, x, eff, SOURCE, f"{name}.{p.rule}:{p.date}#{i}.{j}"))
        close, ni_close = sheet(book, t)
        open_, ni_open = sheet(book, t - 1)
        ni = ni_close - ni_open
        income = {}
        for a, x in chart.items():
            if x["statement"] == "income_statement":
                v = debit_positive(book, a, t) - debit_positive(book, a, t - 1)
                if v:
                    income[a] = -v if x["normal_balance"] == "credit" else v
        direct = {"operating": Fraction(0), "investing": Fraction(0), "financing": Fraction(0)}
        for q in book.postings:
            if q.period == t and "cash" in (q.debit, q.credit):
                rule = q.event.split(".", 1)[1].split(":", 1)[0]
                direct[CF_CLASS[rule]] += q.amount if q.debit == "cash" else -q.amount
        indirect = [("net income", ni)]
        for a in sorted(set(bs_accounts) - {"cash", "retained_earnings", "distributions"}):
            if a in INVESTING_ACCOUNTS | FINANCING_ACCOUNTS:
                # only the part posted without cash (an accrual), from the firm postings of the year
                d = sum((Fraction(p.legs[a]) for _, p in by_period.get(t, []) if a in p.legs and "cash" not in p.legs),
                        Fraction(0))
                if d:
                    indirect.append((f"non-cash change in {a}", -d))
                continue
            d = close[a] - open_[a]
            if d:
                indirect.append((f"change in {a}", -d))
        checks = []
        for when, bal in (([] if t == 0 else [("open", open_)]) + [("close", close)]):
            assets = sum((v for a, v in bal.items() if a in asset_ids), Fraction(0))
            claims = -sum((v for a, v in bal.items() if a not in asset_ids), Fraction(0))
            checks.append((f"assets equal liabilities plus equity ({when})", assets - claims))
        checks.append(("opening cash plus cash postings equals closing cash",
                       open_["cash"] + sum(direct.values(), Fraction(0)) - close["cash"]))
        dist = debit_positive(book, "distributions", t) - debit_positive(book, "distributions", t - 1)
        checks.append(("retained earnings roll by net income less distributions",
                       -open_["retained_earnings"] + ni - dist + close["retained_earnings"]))
        checks.append(("indirect operating cash equals direct operating cash",
                       sum((v for _, v in indirect), Fraction(0)) - direct["operating"]))
        wc = sum((close[a] for a in ("contract_asset", "trade_receivable", "trade_payable", "contract_liability",
                                     "refund_liability_shortfall")), Fraction(0))
        rec = {"balance": {a: v for a, v in close.items() if v}, "income": income, "cash_direct": direct,
               "indirect": indirect, "checks": checks, "working_capital": wc, "net_income": ni}
        if "metrics" in extra:
            rec["metrics"] = extra["metrics"].get(int(ends[t][:4]))
        if t == group.periods - 1:
            rec.update({k: v for k, v in extra.items() if k != "metrics"})
        g.record(name, t, **rec)

    group.register(phase, handler)


RULEBOOKS = {"rollforward": rollforward, "firm_book": firm_book}
