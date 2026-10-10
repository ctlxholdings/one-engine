"""Corporate and buyout pack: a company bought with debt, run from driven lines (plan step 7f).

Built for the Macabacus short-form LBO, a three-statement buyout of a
listed company: a purchase at a premium, purchase accounting, a stack of
term loans, notes and preferred stock, ten years of operations driven by
ratios to sales, a cash sweep, and returns to five holders at an exit
valued at a multiple of EBITDA.

The company is one entity whose book opens on its balance sheet before
the deal (:meth:`~one_engine.entities.Group.open`). Period 0 is the close;
periods 1.. are the forecast years.

Contract types
--------------
``financing_fee``
    A fee paid to arrange a facility (a rate of its commitment), paid when
    the deal closes (a claim the acquisition pays), capitalised, and
    amortised straight-line over a stated number of periods. It runs on
    its own schedule, independent of the facility: a loan repaid early
    keeps amortising its fee (the workbook does this; see LOG).
``preferred_stock``
    Issued for cash (a leg), a dividend at a rate on the opening face
    value, accrued and claimed; a waterfall pays it. The dividend lowers
    equity directly; it is not an expense, so it is below net income.

Rulebooks
---------
``acquisition``
    At the close: the offer (share price, premium, shares, in-the-money
    options exercised), purchase accounting (the old book equity and
    goodwill eliminated, fair-value marks, a deferred tax liability on the
    marks, goodwill as what is left of the purchase price), refinancing,
    transaction costs and financing fees, the sources (loan draws, stated
    issues, excess cash above a minimum, a tax refund), and the residual
    source (the sponsor's equity) that makes sources equal uses. Every
    posting goes through a clearing account that must end at zero.
``operating_plan``
    The forecast lines: each stated for its first period and driven
    afterwards (sales grow at the rate the first period shows over the
    last twelve months; a cost is its first period's ratio to a driver
    line), plus schedules added after the first period (the depreciation
    of a fair-value mark), interest on an opening balance, and working
    capital held at its opening ratio to a driver line.

Lens
----
``exit_returns``
    The holders' returns at an exit: equity value is a multiple of the
    exit period's EBITDA less net debt (cuts of the books); each holder's
    stream is what the company paid it up to the exit, plus its claim then
    outstanding and its fully diluted share of the equity value
    (:mod:`one_engine.holders`). Nothing is posted.
"""
from __future__ import annotations

from fractions import Fraction
from typing import Any, Callable, Dict, List, Mapping

from one_engine.contracts import ClaimSpec, ContractType, FlowSpec, Leg
from one_engine.cuts import Series, balance_cut, cash_flow_cut, pnl_cut
from one_engine.entities import Group
from one_engine.holders import dilute, holder_stream
from one_engine.inputs import InputError, Reader
from one_engine.kernel import Nature, Posting
from one_engine.units import exact


def _at(x, t):
    """A term that is a number or a per-period series, at period ``t``."""
    if isinstance(x, (list, tuple)):
        if t >= len(x):
            raise ValueError(f"series of {len(x)} values has no period {t}")
        return exact(x[t])
    return exact(x)


def _total(x) -> Fraction:
    """A number, or a list of numbers summed (the last four quarters of a line)."""
    return sum((exact(v) for v in x), Fraction(0)) if isinstance(x, (list, tuple)) else exact(x)


# ── contract types ────────────────────────────────────────────────────────

def _fee_amount(terms) -> Fraction:
    return exact(terms["rate"]) * exact(terms["facility"].values()["commitment"])


def _financing_fee_amounts(terms, t, bal=None):
    paid_at, n = int(terms["paid_at"]), int(terms["periods"])
    if bal is None or not paid_at < t <= paid_at + n:
        return {}
    # what is left over the periods left: straight-line, and exactly nothing left after the last
    return {"amortise": bal.opening("borrower", "{id}.deferred") / (paid_at + n - t + 1)}


def _financing_fee_claims(terms, t, bal):
    if t != int(terms["paid_at"]):
        return {}
    return {"fee": max(Fraction(0), _fee_amount(terms) - bal("borrower", "{id}.deferred"))}


FINANCING_FEE = ContractType(
    name="financing_fee",
    terms=("facility", "rate", "periods", "paid_at"),
    parties=("borrower",),
    accounts={"borrower": (("cash", Nature.ASSET), ("{id}.deferred", Nature.ASSET),
                           ("{id}.amortisation", Nature.EXPENSE))},
    flows=(FlowSpec("amortise", "accrue", (Leg("borrower", "{id}.amortisation", "{id}.deferred", (+1, -1)),)),),
    amounts=_financing_fee_amounts,
    claim_specs=(ClaimSpec("fee", "pay", Leg("borrower", "{id}.deferred", "cash", (+1, -1))),),
    claims=_financing_fee_claims,
    refs=("facility",),
)


def _preferred_amounts(terms, t, bal=None):
    if bal is None or t < int(terms["from"]):
        return {}
    return {"dividend": _at(terms["rate"], t) * bal.opening("issuer", "{id}.capital")}


PREFERRED_STOCK = ContractType(
    name="preferred_stock",
    terms=("rate", "from"),
    parties=("issuer",),
    accounts={"issuer": (("cash", Nature.ASSET), ("{id}.capital", Nature.EQUITY), ("{id}.dividends", Nature.EQUITY),
                         ("{id}.dividend_payable", Nature.LIABILITY))},
    flows=(FlowSpec("dividend", "accrue", (Leg("issuer", "{id}.dividends", "{id}.dividend_payable", (-1, +1)),)),),
    amounts=_preferred_amounts,
    claim_specs=(ClaimSpec("dividend", "pay", Leg("issuer", "{id}.dividend_payable", "cash", (-1, -1))),),
    claims=lambda terms, t, bal: {"dividend": bal("issuer", "{id}.dividend_payable")},
    legs={"issue": Leg("issuer", "cash", "{id}.capital", (+1, +1)),
          "redeem": Leg("issuer", "{id}.capital", "cash", (-1, -1))},
)

TYPES = {t.name: t for t in (FINANCING_FEE, PREFERRED_STOCK)}


# ── acquisition ───────────────────────────────────────────────────────────

def acquisition(group: Group, r: Reader, ctx: Mapping[str, Any]):
    """Install the close of a buyout; see the module docstring.

    Case keys: ``entity``, ``cash``, ``phase``, ``at``, ``tax_rate``,
    ``offer`` (``share_price``, ``premium``, ``basic_shares``, ``options``:
    a list of ``{number, strike}``, exercised when the strike is at or
    below the offer price), ``book_equity`` (the equity accounts the
    purchase eliminates), ``goodwill`` (written off and replaced),
    ``marks`` (``{account, amount}`` or ``{account, share_of_excess}``,
    the excess being the purchase price over book equity), ``deferred_tax``
    (the account that takes the tax on the marks), ``refinance`` (debt
    accounts repaid at close), ``min_cash`` (the cash left in the company;
    the rest is a source), ``transaction_costs`` (``expense`` and ``items``:
    an amount or ``{rate}`` of the equity purchase price each),
    ``tax_benefit`` (the income account of the tax refund on costs and
    fees), ``financing_fees`` (claims paid at close), ``sources`` (loan
    draw claims, and legs with an ``amount`` and an optional
    ``issue_cost: {rate, account}`` netted from the proceeds) and
    ``residual`` (the leg that closes the gap: the sponsor's equity).

    Tax at close follows the workbook: transaction costs, capitalised
    financing fees and issue costs are all deducted when the deal closes,
    and the tax they save is refunded in cash then. The refund on costs
    is income; on the capitalised fees it is a deferred tax liability (the
    fees' tax base is nil once deducted; ``income_tax`` with
    ``nil_tax_base`` unwinds it as they are amortised); on issue costs it
    goes back to the equity account that bore them. All at ``tax_rate``,
    the deal's rate.
    """
    entity, cash, phase, at = r.take("entity"), r.take("cash"), r.take("phase"), int(r.take("at"))
    rate, unit = exact(r.take("tax_rate")), ctx["currency"]
    o = Reader(r.take("offer"), source="acquisition.offer")
    share_price, premium, basic = exact(o.take("share_price")), exact(o.take("premium")), exact(o.take("basic_shares"))
    options = []
    for i, raw in enumerate(o.take("options")):
        orr = Reader(raw, source=f"acquisition.offer.options[{i}]")
        options.append((exact(orr.take("number")), exact(orr.take("strike"))))
        orr.finish()
    o.finish()
    book_equity, goodwill = list(r.take("book_equity")), r.take("goodwill")
    marks = []
    for i, raw in enumerate(r.take("marks")):
        mr = Reader(raw, source=f"acquisition.marks[{i}]")
        acct = mr.take("account")
        marks.append((acct, ("amount", exact(mr.take("amount"))) if "amount" in raw
                      else ("share_of_excess", exact(mr.take("share_of_excess")))))
        mr.finish()
    deferred_tax, refinance = r.take("deferred_tax"), list(r.take("refinance"))
    min_cash = exact(r.take("min_cash"))
    tr = Reader(r.take("transaction_costs"), source="acquisition.transaction_costs")
    cost_expense = tr.take("expense")
    cost_items = {}
    for k, v in tr.take("items").items():
        cost_items[k] = ("rate", exact(v["rate"])) if isinstance(v, Mapping) else ("amount", exact(v))
        if isinstance(v, Mapping) and set(v) != {"rate"}:
            raise InputError(f"acquisition.transaction_costs.items.{k}: an item is an amount or {{rate}}")
    tr.finish()
    tax_benefit, fees = r.take("tax_benefit"), list(r.take("financing_fees"))
    sources = []
    for i, raw in enumerate(r.take("sources")):
        sr = Reader(raw, source=f"acquisition.sources[{i}]")
        if "claim" in raw:
            sources.append(("claim", sr.take("claim"), None, None))
        else:
            ic = sr.take_or_default("issue_cost", None, reason="no cost netted from the proceeds").value
            cost = None
            if ic is not None:
                ir = Reader(ic, source=f"acquisition.sources[{i}].issue_cost")
                cost = (exact(ir.take("rate")), ir.take("account"))
                ir.finish()
            sources.append(("leg", sr.take("leg"), exact(sr.take("amount")), cost))
        sr.finish()
    residual = r.take("residual")
    clearing = f"{ctx['name']}.purchase"

    group.declare(entity, clearing, Nature.ASSET, unit)
    group.declare(entity, cost_expense, Nature.EXPENSE, unit)
    group.declare(entity, tax_benefit, Nature.INCOME, unit)
    group.declare(entity, deferred_tax, Nature.LIABILITY, unit)
    for _, _, _, cost in sources:
        if cost:
            group.declare(entity, cost[1], Nature.EQUITY, unit)
    # what other rulebooks read once the deal is done (an operating plan depreciating a mark)
    deal: Dict[str, Any] = {"marks": {}, "marked": {acct for acct, _ in marks}}
    ctx["deals"][ctx["name"]] = deal

    def handler(g: Group, t: int) -> None:
        if t != at:
            return
        book = g.book(entity)
        n = [0]

        def post(debit, credit, amount, effect, what):
            if amount:
                n[0] += 1
                g.post(entity, Posting(t, phase, debit, credit, amount, {debit: effect[0], credit: effect[1]},
                                       "acquisition", f"{ctx['name']}.{what}:{t}"))

        cash_before = book.balance(cash, t)
        # the offer: options at or below the offer price are exercised and bought out with the shares
        offer = share_price * (1 + premium)
        itm = [(num, k) for num, k in options if k <= offer]
        fdso = basic + sum((num for num, _ in itm), Fraction(0))
        equity_price = offer * fdso
        option_proceeds = sum((num * k for num, k in itm), Fraction(0))
        price = equity_price - option_proceeds
        # purchase accounting, through the clearing account
        post(clearing, cash, equity_price, (+1, -1), "equity_purchase_price")
        post(cash, clearing, option_proceeds, (+1, -1), "option_proceeds")
        book_value = Fraction(0)
        for acct in book_equity:
            x = book.balance(acct, t)
            book_value += x
            if x > 0:
                post(acct, clearing, x, (-1, -1), f"eliminate.{acct}")
            elif x < 0:
                post(clearing, acct, -x, (+1, +1), f"eliminate.{acct}")
        old_goodwill = book.balance(goodwill, t)
        post(clearing, goodwill, old_goodwill, (+1, -1), "goodwill_written_off")
        excess = price - book_value
        for acct, (how, v) in marks:
            mark = v if how == "amount" else v * excess
            if mark < 0:
                raise InputError(f"acquisition: a mark down of {acct} ({float(mark):,.4f}) is not built")
            deal["marks"][acct] = mark
            post(acct, clearing, mark, (+1, -1), f"mark.{acct}")
        dtl = rate * sum(deal["marks"].values(), Fraction(0))
        post(clearing, deferred_tax, dtl, (+1, +1), "deferred_tax_on_marks")
        new_goodwill = book.balance(clearing, t)        # what the price pays beyond the net assets marked
        if new_goodwill < 0:
            raise InputError(f"acquisition: a bargain purchase ({float(new_goodwill):,.4f}) is not built")
        post(goodwill, clearing, new_goodwill, (+1, -1), "goodwill")
        # refinancing, costs and fees
        refinanced = Fraction(0)
        for acct in refinance:
            x = book.balance(acct, t)
            refinanced += x
            post(acct, cash, x, (-1, -1), f"refinance.{acct}")
        costs = sum(((v * equity_price if how == "rate" else v) for how, v in cost_items.values()), Fraction(0))
        post(cost_expense, cash, costs, (+1, -1), "transaction_costs")
        claims = g.claims(t)
        capitalised = Fraction(0)
        for ref in fees:
            if ref in claims:
                capitalised += claims[ref].due
                g.settle(claims[ref], claims[ref].due, phase)
        # sources
        raised, netted = {}, Fraction(0)
        for kind, ref, amount, cost in sources:
            if kind == "claim":
                raised[ref] = claims[ref].due if ref in claims else Fraction(0)
                if ref in claims:
                    g.settle(claims[ref], raised[ref], phase)
            else:
                cid, _, leg = ref.partition(".")
                g.post_leg(g.contract(cid), leg, amount, t, phase)
                raised[ref] = amount
                if cost:
                    fee = cost[0] * amount
                    netted += fee
                    post(cost[1], cash, fee, (-1, -1), f"issue_cost.{cid}")
                    post(cash, cost[1], rate * fee, (+1, +1), f"issue_cost_tax.{cid}")
        # the tax the deductions save, refunded now: on the costs it is income; on the capitalised fees it
        # is a deferred tax liability, since the asset's tax base is nil once its cost is deducted
        post(cash, tax_benefit, rate * costs, (+1, +1), "tax_refund.costs")
        post(cash, deferred_tax, rate * capitalised, (+1, +1), "tax_refund.fees")
        refund = rate * (costs + capitalised)
        uses = {"equity_purchase_price": equity_price, "refinance": refinanced,
                "financing_fees": capitalised + netted, "transaction_costs": costs}
        found = {"excess_cash": cash_before - min_cash, "option_proceeds": option_proceeds, **raised,
                 "tax_refund": refund + rate * netted}
        gap = sum(uses.values(), Fraction(0)) - sum(found.values(), Fraction(0))
        if gap < 0:
            raise InputError(f"acquisition: sources exceed uses by {float(-gap):,.4f}; the residual cannot be negative")
        cid, _, leg = residual.partition(".")
        g.post_leg(g.contract(cid), leg, gap, t, phase)
        found[residual] = gap
        deal.update(offer_price=offer, fdso=fdso, equity_purchase_price=equity_price, option_proceeds=option_proceeds,
                    purchase_price=price, book_value=book_value, excess=excess, dtl=dtl, goodwill=new_goodwill,
                    sources=found, uses=uses)
        g.record("deal", t, **{k: v for k, v in deal.items()})

    def check() -> List[str]:
        """The clearing account ends at zero, and the deal's cash leaves exactly the minimum (sources = uses)."""
        book = group.book(entity)
        out = []
        if book.balance(clearing, at) != 0:
            out.append(f"clearing account {clearing} ends at {float(book.balance(clearing, at)):,.6g}")
        moved = sum(((p.amount if p.debit == cash else -p.amount) for p in book.postings
                     if p.period == at and p.phase == phase and cash in (p.debit, p.credit)), Fraction(0))
        if book.opening(cash, at) + moved != min_cash:
            out.append(f"the deal leaves {float(book.opening(cash, at) + moved):,.6g} of cash, not {float(min_cash):,.6g}")
        return out

    group.register(phase, handler)
    return {"sources_equal_uses": check}


# ── operating plan ────────────────────────────────────────────────────────

# kind -> (nature of the line's account, nature of the account it posts against or None for cash,
#          effect on the line's account, effect on the other end), with the line's account the debit
# except for income, where cash is the debit
_KINDS = {
    "income": (Nature.INCOME, None),
    "expense": (Nature.EXPENSE, None),
    "share_compensation": (Nature.EXPENSE, Nature.EQUITY),
    "depreciation": (Nature.EXPENSE, Nature.ASSET),
    "investment": (Nature.ASSET, None),
}


def operating_plan(group: Group, r: Reader, ctx: Mapping[str, Any]) -> None:
    """Install the forecast lines and working capital; see the module docstring.

    Case keys: ``entity``, ``cash``, ``phase``, ``from``, ``until``,
    ``lines`` and ``working_capital``.

    A line has a ``kind`` (``income``: cash in; ``expense``: cash out;
    ``share_compensation``: an expense credited to an equity account
    ``against``; ``depreciation``: an expense credited to the asset
    ``against``; ``investment``: an asset bought for cash), an ``account``,
    and one amount rule:

    * ``first`` and ``growth_from``: the first period is ``first``; each
      later one grows by ``first / growth_from`` (the first year over the
      last twelve months; ``growth_from`` may be the four quarters);
    * ``first`` and ``ratio_of``: the first period is ``first``; later ones
      are ``first / <line>.first`` times that line's amount, plus each
      ``plus`` schedule (``{mark: <deal>.<account>, periods}``: the mark the
      acquisition made, straight-line over ``periods`` from ``from``). The
      first period is ``first`` as stated, whatever the schedules hold for
      it: a stated first year replaces the formula, as a typed cell does;
    * ``rate`` and ``on_balance``: the rate of the period (a series) times the
      opening balance of an account (interest on cash).

    Working capital: each account (``side``: asset or liability) is held at
    its balance before ``from`` over ``base`` (the driver's last twelve
    months, a number or the four quarters), times the driver line
    (``of``), from ``from``; the change moves cash.
    """
    entity, cash, phase = r.take("entity"), r.take("cash"), r.take("phase")
    start, until = int(r.take("from")), int(r.take("until"))
    unit = ctx["currency"]
    lines: Dict[str, Dict[str, Any]] = {}
    for name, raw in r.take("lines").items():
        lr = Reader(raw, source=f"operating_plan.lines.{name}")
        kind = lr.take("kind")
        if kind not in _KINDS:
            raise InputError(f"operating_plan.lines.{name}: kind must be one of {sorted(_KINDS)}")
        line: Dict[str, Any] = {"kind": kind, "account": lr.take("account")}
        if _KINDS[kind][1] is not None:
            line["against"] = lr.take("against")
        if "rate" in raw:
            line["rule"] = "rate"
            line["rate"], line["on"] = lr.take("rate"), lr.take("on_balance")
        else:
            line["first"] = exact(lr.take("first"))
            if "growth_from" in raw:
                line["rule"], line["growth"] = "growth", line["first"] / _total(lr.take("growth_from"))
            else:
                line["rule"], line["of"] = "ratio", lr.take("ratio_of")
                line["plus"] = []
                for i, p in enumerate(lr.take_or_default("plus", [], reason="nothing added to the ratio").value):
                    pr = Reader(p, source=f"operating_plan.lines.{name}.plus[{i}]")
                    deal_name, _, acct = pr.take("mark").partition(".")
                    line["plus"].append((deal_name, acct, int(pr.take("periods"))))
                    pr.finish()
        lr.finish()
        lines[name] = line
    for name, line in lines.items():
        if line["rule"] == "ratio" and (line["of"] not in lines or "first" not in lines[line["of"]]):
            raise InputError(f"operating_plan.lines.{name}: ratio_of names '{line['of']}', not a line with a first value")
        for deal_name, acct, _ in line.get("plus", []):
            if deal_name not in ctx.get("deals", {}):
                raise InputError(f"operating_plan.lines.{name}: no acquisition '{deal_name}' is declared before it")
            if acct not in ctx["deals"][deal_name]["marked"]:
                raise InputError(f"operating_plan.lines.{name}: acquisition '{deal_name}' marks no '{acct}'")
        nat, against = _KINDS[line["kind"]]
        group.declare(entity, line["account"], nat, unit)
        if against is not None:
            group.declare(entity, line["against"], against, unit)
    wc = []
    for acct, raw in r.take("working_capital").items():
        wr = Reader(raw, source=f"operating_plan.working_capital.{acct}")
        side = wr.take("side")
        if side not in ("asset", "liability"):
            raise InputError(f"operating_plan.working_capital.{acct}: side must be asset or liability")
        of, base = wr.take("of"), _total(wr.take("base"))
        wr.finish()
        if of not in lines:
            raise InputError(f"operating_plan.working_capital.{acct}: '{of}' is not a line")
        group.declare(entity, acct, Nature.ASSET if side == "asset" else Nature.LIABILITY, unit)
        wc.append((acct, side, of, base))
    amounts: Dict[int, Dict[str, Fraction]] = {}

    def amount(g: Group, name: str, t: int) -> Fraction:
        """A line's amount in ``t`` (memoised: a ratio line reads its driver's)."""
        got = amounts.setdefault(t, {})
        if name in got:
            return got[name]
        line = lines[name]
        if line["rule"] == "rate":
            x = _at(line["rate"], t) * g.book(entity).opening(line["on"], t)
        elif t == start:
            x = line["first"]
        elif line["rule"] == "growth":
            x = amount(g, name, t - 1) * line["growth"]
        else:
            x = line["first"] / lines[line["of"]]["first"] * amount(g, line["of"], t)
            for deal_name, acct, n in line["plus"]:
                if start <= t < start + n:
                    x += ctx["deals"][deal_name]["marks"][acct] / n
        if x < 0:
            raise InputError(f"operating_plan: line '{name}' is negative ({float(x):,.4f}) in period {t}")
        got[name] = x
        return x

    def handler(g: Group, t: int) -> None:
        if not start <= t <= until:
            return
        book = g.book(entity)

        def post(debit, credit, x, effect, what):
            if x:
                g.post(entity, Posting(t, phase, debit, credit, x, {debit: effect[0], credit: effect[1]},
                                       "operating_plan", f"plan.{what}:{t}"))

        for name, line in lines.items():
            x, acct, kind = amount(g, name, t), line["account"], line["kind"]
            if kind == "income":
                post(cash, acct, x, (+1, +1), name)
            elif kind in ("expense", "investment"):
                post(acct, cash, x, (+1, -1), name)
            elif kind == "share_compensation":
                post(acct, line["against"], x, (+1, +1), name)
            else:                                   # depreciation: the asset is lowered
                post(acct, line["against"], x, (+1, -1), name)
        for acct, side, of, base in wc:
            ratio = book.balance(acct, start - 1) / base     # the balance before the forecast over the LTM driver
            change = ratio * amount(g, of, t) - book.balance(acct, t)
            if side == "asset":
                if change > 0:
                    post(acct, cash, change, (+1, -1), f"wc.{acct}")
                else:
                    post(cash, acct, -change, (+1, -1), f"wc.{acct}")
            elif change > 0:
                post(cash, acct, change, (+1, +1), f"wc.{acct}")
            else:
                post(acct, cash, -change, (-1, -1), f"wc.{acct}")

    group.register(phase, handler)


RULEBOOKS = {"acquisition": acquisition, "operating_plan": operating_plan}


# ── exit returns (a lens) ─────────────────────────────────────────────────

def exit_returns(group: Group, r: Reader, ctx: Mapping[str, Any]) -> Dict[str, Any]:
    """Each holder's stream to an exit, and the valuation behind it. Posts nothing.

    Case keys: ``entity``, ``cash``, ``period`` (the exit), ``multiple``,
    ``ebitda`` and ``net_debt`` (``{scheme, include}``: a P&L cut and a
    balance cut of the books), ``flows`` (the scheme that classifies the
    company's cash flows by holder), ``claims_scheme`` (the balance-sheet
    scheme in which a holder's claim is read), ``holders`` (holder ->
    ``{classes, claim}``: the classes of its cash flows in ``flows``, and
    the ``claims_scheme`` classes of what it is owed at the exit, if any) and
    ``ownership`` (``paid``: the holders who bought the common equity, pro
    rata to what they paid at the deal period; ``at``: that period;
    ``layers``: the dilution layers in order).

    Returns ``{"series": {holder: Series}, "values": {...}}``: the streams,
    and the exit EBITDA, enterprise value, net debt, equity value and the
    fully diluted ownership.
    """
    book = group.book(r.take("entity"))
    cash, exit_t, multiple = r.take("cash"), int(r.take("period")), exact(r.take("multiple"))
    schemes = ctx["schemes"]

    def cut(key, fn):
        cr = Reader(r.take(key), source=f"exit_returns.{key}")
        s = fn(book, schemes[cr.take("scheme")], cr.take("include"), name=key)
        cr.finish()
        return s

    ebitda, net_debt = cut("ebitda", pnl_cut), cut("net_debt", balance_cut)
    flows_scheme = schemes[r.take("flows")]
    bs = schemes[r.take("claims_scheme")]
    holders = {}
    for h, raw in r.take("holders").items():
        hr = Reader(raw, source=f"exit_returns.holders.{h}")
        holders[h] = (hr.take("classes"), hr.take("claim"))
        hr.finish()
    orr = Reader(r.take("ownership"), source="exit_returns.ownership")
    paid_by, paid_at, layers = orr.take("paid"), int(orr.take("at")), orr.take("layers")
    orr.finish()
    flows = {h: cash_flow_cut(book, cash, flows_scheme, classes, name=h) for h, (classes, _) in holders.items()}
    for h in paid_by:
        if h not in flows:
            raise InputError(f"exit_returns.ownership: '{h}' is not a holder")
    own = dilute({h: flows[h].values[paid_at] for h in paid_by}, layers)
    unknown = set(own) - set(holders)
    if unknown:
        raise InputError(f"exit_returns.ownership: {sorted(unknown)} own a share but are not holders")
    ev = multiple * ebitda.values[exit_t]
    equity = ev - net_debt.values[exit_t]
    series = {}
    for h, (classes, claim) in holders.items():
        owed = balance_cut(book, bs, claim).values[exit_t] if claim else Fraction(0)
        series[h] = Series(h, holder_stream(flows[h].values, exit_t, owed, own.get(h, Fraction(0)), equity),
                           flows[h].states | (ebitda.states | net_debt.states if h in own else frozenset()))
    return {"series": series,
            "values": {"ebitda": ebitda.values[exit_t], "enterprise_value": ev, "net_debt": net_debt.values[exit_t],
                       "equity_value": equity, "ownership": own}}


LENSES: Dict[str, Callable] = {"exit_returns": exit_returns}
