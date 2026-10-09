"""Contract types (packs for module card 6).

Each type is wiring plus one amounts function; see
:mod:`one_engine.contracts` for the shape. Account natures follow the
kernel: assets and expenses are debit-natured, equity and income
credit-natured.

Built so far, for the ERIA case (plan step 6):

* ``equity_funding``   an owner outside the model pays in capital once;
* ``one_off_purchase`` a buyer pays for equipment once (seller outside);
* ``shared_savings``   an energy-savings agreement: measured savings
  (kWh x tariff x performance) accrue to the client, which pays the
  provider a fixed share of them each period of the term.

Added for Averta's 100MW SPV (plan step 6) and the 50MW feature case
(step 7):

* ``term_loan``         drawn as a source, interest accrued on the opening
  balance, a commitment fee on the undrawn amount while available;
  interest, fee and principal claimed each period; an unpaid amount stays
  due (arrears);
* ``shareholder_loan``  the same, except that unpaid interest is
  capitalised at period close (PIK) into its own account, repaid ahead of
  principal against the scheduled balance;
* ``offtake``           volume x price into revenue, collected on payment
  terms (a receivable);
* ``operating_cost``    a fixed annual cost plus a rate per unit, with
  recoverable VAT, paid on payment terms (a payable);
* ``capex``             a construction spend schedule into CWIP, with
  recoverable VAT;
* ``debt_reserve``      a reserve sized on a loan's next scheduled debt
  service: prefunded during construction, topped up as a claim, released
  as a flow when above target;
* ``reserve_letter_of_credit`` the same requirement covered by a letter of
  credit: no cash is set aside; a fee on the requirement is claimed;
* ``sponsor_equity``    contributions and distributions as named legs whose
  amounts a funding plan or a waterfall decides.

Modelling note on ``shared_savings``: the client's savings are booked as an
income line ("avoided energy cost"), i.e. a with-versus-without view of the
client's utility bill. A lens that restates the client against its baseline
would read the same postings.
"""
from __future__ import annotations

from one_engine.contracts import ClaimSpec, ContractType, FlowSpec, Leg
from one_engine.kernel import Nature
from one_engine.units import exact


def _once(terms, t, bal=None):
    return {"payment": exact(terms["amount"])} if t == int(terms["period"]) else {}


EQUITY_FUNDING = ContractType(
    name="equity_funding",
    terms=("amount", "period"),
    parties=("issuer",),
    accounts={"issuer": (("cash", Nature.ASSET), ("share_capital", Nature.EQUITY))},
    flows=(FlowSpec("payment", "accrue", (Leg("issuer", "cash", "share_capital", (+1, +1)),)),),
    amounts=_once,
)

ONE_OFF_PURCHASE = ContractType(
    name="one_off_purchase",
    terms=("amount", "period"),
    parties=("buyer",),
    accounts={"buyer": (("cash", Nature.ASSET), ("equipment", Nature.ASSET))},
    flows=(FlowSpec("payment", "accrue", (Leg("buyer", "equipment", "cash", (+1, -1)),)),),
    amounts=_once,
)


def _shared_savings(terms, t, bal=None):
    start, n = int(terms["start"]), int(terms["terms"])
    if not start <= t < start + n:
        return {}
    savings = exact(terms["kwh"]) * exact(terms["tariff"]) * exact(terms["performance"])
    return {"savings": savings, "fee": exact(terms["split"]) * savings}


SHARED_SAVINGS = ContractType(
    name="shared_savings",
    terms=("kwh", "tariff", "performance", "split", "start", "terms"),
    parties=("provider", "client"),
    accounts={
        "client": (("cash", Nature.ASSET), ("avoided_energy_cost", Nature.INCOME),
                   ("shared_savings_fee", Nature.EXPENSE)),
        "provider": (("cash", Nature.ASSET), ("shared_savings_revenue", Nature.INCOME)),
    },
    flows=(
        FlowSpec("savings", "accrue", (Leg("client", "cash", "avoided_energy_cost", (+1, +1)),)),
        FlowSpec("fee", "settle", (Leg("client", "shared_savings_fee", "cash", (+1, -1)),
                                   Leg("provider", "cash", "shared_savings_revenue", (+1, +1)))),
    ),
    amounts=_shared_savings,
)

# ── project finance (Averta 100MW SPV, 50MW feature case) ────────────────

def _at(x, t):
    """A term that is a number or a per-period series, at period ``t``."""
    if isinstance(x, (list, tuple)):
        if t >= len(x):
            raise ValueError(f"series of {len(x)} values has no period {t}")
        return exact(x[t])
    return exact(x)


def _days(terms):
    return exact(terms["days_per_period"]) / exact(terms["day_count_basis"])


def _refund_at(t, terms):
    """Period in which VAT paid in period ``t`` comes back.

    ``vat_refund_lag`` periods later, but never after the contract's
    ``end``: what is still outstanding at the end is settled then, so the
    receivable winds down inside the horizon (declared convention).
    """
    return min(t + int(terms["vat_refund_lag"]), int(terms["end"]))


def _vat_refund(terms, t, base):
    """VAT coming back in ``t``: rate x the bases of every period whose refund falls in ``t``."""
    return sum((exact(terms["vat_rate"]) * base(p) for p in range(0, t + 1) if _refund_at(p, terms) == t),
               exact(0))


# Loans ------------------------------------------------------------------

def loan_schedule(terms, t):
    """Scheduled (principal, interest, closing balance) of a loan in ``t``, assuming full draw.

    The scheduled balance is the commitment until ``repay_from``, then falls
    in ``repay_periods`` equal steps to zero (35M over 36 quarters has no
    exact decimal amount, so the count is the term). Interest is the scheduled opening
    balance x rate x days_per_period / day_count_basis, from ``rate_from``.
    A debt reserve is sized on this schedule, not on the actual balance (a
    reserve that shrank because a payment was missed would defeat its purpose).
    """
    def target(p):
        if p < int(terms["repay_from"]):
            return exact(terms["commitment"])
        step = exact(terms["commitment"]) / int(terms["repay_periods"])
        return max(exact(0), exact(terms["commitment"]) - step * (p - int(terms["repay_from"]) + 1))
    opening = target(t - 1) if t > 0 else exact(terms["commitment"])
    interest = opening * exact(terms["rate"]) * _days(terms) if t >= int(terms["rate_from"]) else exact(0)
    return opening - target(t), interest, target(t)


def _loan(name, capitalise):
    """A term loan, or (``capitalise``) a shareholder loan whose unpaid interest is capitalised.

    Capitalised interest has its own account. The debt account then holds
    only what was drawn less principal repaid, which is what the
    commitment, the undrawn fee and the draw limit are measured against;
    interest accrues on both. Capitalised interest is repaid ahead of
    principal, against the same scheduled balance.
    """
    borrower = [("cash", Nature.ASSET), ("{id}.debt", Nature.LIABILITY), ("{id}.interest", Nature.EXPENSE),
                ("{id}.interest_payable", Nature.LIABILITY), ("{id}.fees", Nature.EXPENSE),
                ("{id}.fee_payable", Nature.LIABILITY)]
    if capitalise:
        borrower.append(("{id}.capitalised_interest", Nature.LIABILITY))

    def owed(bal, opening=False):
        read = bal.opening if opening else bal
        return read("borrower", "{id}.debt") + (read("borrower", "{id}.capitalised_interest") if capitalise else 0)

    def amounts(terms, t, bal=None):
        if bal is None:
            return {}
        out = {}
        if t >= int(terms["rate_from"]):
            out["interest"] = owed(bal, opening=True) * exact(terms["rate"]) * _days(terms)
        if t <= int(terms["available_until"]):
            undrawn = exact(terms["commitment"]) - bal.opening("borrower", "{id}.debt")
            out["commitment_fee"] = undrawn * exact(terms["commitment_fee_rate"]) * _days(terms)
        return out

    def claims(terms, t, bal):
        debt = bal("borrower", "{id}.debt")
        _, _, target = loan_schedule(terms, t)
        due = {"draw": max(exact(0), exact(terms["commitment"]) - debt) if t <= int(terms["available_until"]) else exact(0),
               "interest": bal("borrower", "{id}.interest_payable"),
               "fee": bal("borrower", "{id}.fee_payable")}
        excess = max(exact(0), owed(bal) - target) if t >= int(terms["repay_from"]) else exact(0)
        if capitalise:
            due["capitalised"] = min(bal("borrower", "{id}.capitalised_interest"), excess)
            excess -= due["capitalised"]
        due["principal"] = excess
        return due

    specs = [ClaimSpec("draw", "source", Leg("borrower", "cash", "{id}.debt", (+1, +1))),
             ClaimSpec("interest", "pay", Leg("borrower", "{id}.interest_payable", "cash", (-1, -1)),
                       shortfall=Leg("borrower", "{id}.interest_payable", "{id}.capitalised_interest", (-1, +1))
                       if capitalise else None),
             ClaimSpec("fee", "pay", Leg("borrower", "{id}.fee_payable", "cash", (-1, -1)))]
    if capitalise:
        specs.append(ClaimSpec("capitalised", "pay", Leg("borrower", "{id}.capitalised_interest", "cash", (-1, -1))))
    specs.append(ClaimSpec("principal", "pay", Leg("borrower", "{id}.debt", "cash", (-1, -1))))
    return ContractType(
        name=name,
        terms=("commitment", "rate", "rate_from", "days_per_period", "day_count_basis", "available_until",
               "commitment_fee_rate", "repay_from", "repay_periods"),
        parties=("borrower",),
        accounts={"borrower": tuple(borrower)},
        flows=(FlowSpec("interest", "accrue", (Leg("borrower", "{id}.interest", "{id}.interest_payable", (+1, +1)),)),
               FlowSpec("commitment_fee", "accrue", (Leg("borrower", "{id}.fees", "{id}.fee_payable", (+1, +1)),))),
        amounts=amounts,
        claim_specs=tuple(specs),
        claims=claims,
    )


TERM_LOAN = _loan("term_loan", capitalise=False)
SHAREHOLDER_LOAN = _loan("shareholder_loan", capitalise=True)


# Sales and costs on payment terms ----------------------------------------

def _terms_target(amount, terms, t):
    """Balance left unpaid at period end under ``payment_days`` terms (zero from ``end``).

    Annualised flow x payment_days / days_per_year: the target-balance
    method on a period grid that cannot see individual invoices.
    """
    if t >= int(terms["end"]):
        return exact(0)
    return amount * int(terms["periods_per_year"]) * exact(terms["payment_days"]) / exact(terms["days_per_year"])


def _offtake(terms, t, bal=None):
    sale = _at(terms["volume"], t) * _at(terms["price"], t) if t <= int(terms["end"]) else exact(0)
    out = {"sale": sale}
    if bal is not None:
        out["collect"] = bal("seller", "{id}.receivable") + sale - _terms_target(sale, terms, t)
    return out


OFFTAKE = ContractType(
    name="offtake",
    terms=("volume", "price", "payment_days", "days_per_year", "periods_per_year", "end"),
    parties=("seller",),
    accounts={"seller": (("cash", Nature.ASSET), ("{id}.revenue", Nature.INCOME), ("{id}.receivable", Nature.ASSET))},
    flows=(FlowSpec("sale", "operate", (Leg("seller", "{id}.receivable", "{id}.revenue", (+1, +1)),)),
           FlowSpec("collect", "operate", (Leg("seller", "cash", "{id}.receivable", (+1, -1)),))),
    amounts=_offtake,
)


def _cost_base(terms, t):
    if not int(terms["start"]) <= t <= int(terms["end"]):
        return exact(0)
    return exact(terms["annual"]) / int(terms["periods_per_year"]) + exact(terms["unit_rate"]) * _at(terms["volume"], t)


def _operating_cost(terms, t, bal=None):
    cost = _cost_base(terms, t)
    vat = exact(terms["vat_rate"]) * cost
    out = {"cost": cost, "vat": vat, "vat_refund": _vat_refund(terms, t, lambda p: _cost_base(terms, p))}
    if bal is not None:
        out["payment"] = bal("payer", "{id}.payable") + cost + vat - _terms_target(cost + vat, terms, t)
    return out


OPERATING_COST = ContractType(
    name="operating_cost",
    terms=("annual", "unit_rate", "volume", "periods_per_year", "start", "end", "vat_rate", "vat_refund_lag",
           "payment_days", "days_per_year"),
    parties=("payer",),
    accounts={"payer": (("cash", Nature.ASSET), ("{id}.expense", Nature.EXPENSE), ("{id}.payable", Nature.LIABILITY),
                        ("vat_receivable", Nature.ASSET))},
    flows=(FlowSpec("cost", "operate", (Leg("payer", "{id}.expense", "{id}.payable", (+1, +1)),)),
           FlowSpec("vat", "operate", (Leg("payer", "vat_receivable", "{id}.payable", (+1, +1)),)),
           FlowSpec("payment", "operate", (Leg("payer", "{id}.payable", "cash", (-1, -1)),)),
           FlowSpec("vat_refund", "operate", (Leg("payer", "cash", "vat_receivable", (+1, -1)),))),
    amounts=_operating_cost,
)


def _capex(terms, t, bal=None):
    spend = (lambda p: _at(terms["schedule"], p) if p < len(terms["schedule"]) else exact(0))
    return {"spend": spend(t), "vat": exact(terms["vat_rate"]) * spend(t), "vat_refund": _vat_refund(terms, t, spend)}


CAPEX = ContractType(
    name="capex",
    terms=("schedule", "vat_rate", "vat_refund_lag", "end"),
    parties=("owner",),
    accounts={"owner": (("cash", Nature.ASSET), ("cwip", Nature.ASSET), ("vat_receivable", Nature.ASSET))},
    flows=(FlowSpec("spend", "fund", (Leg("owner", "cwip", "cash", (+1, -1)),)),
           FlowSpec("vat", "fund", (Leg("owner", "vat_receivable", "cash", (+1, -1)),)),
           FlowSpec("vat_refund", "operate", (Leg("owner", "cash", "vat_receivable", (+1, -1)),))),
    amounts=_capex,
)


# Reserves ---------------------------------------------------------------

def _reserve_target(terms, t):
    loan = terms["covers"]
    if t < int(terms["from"]):
        return None
    return sum((sum(loan_schedule(loan.values(), p)[:2]) for p in range(t + 1, t + 1 + int(terms["periods_forward"]))),
               exact(0))


def _reserve_amounts(terms, t, bal=None):
    out = {}
    if t < len(terms["prefund"]):
        out["prefund"] = _at(terms["prefund"], t)
    target = _reserve_target(terms, t)
    if bal is not None and target is not None:
        out["release"] = max(exact(0), bal("holder", "{id}") - target)
    return out


def _reserve_claims(terms, t, bal):
    target = _reserve_target(terms, t)
    return {"top_up": max(exact(0), target - bal("holder", "{id}"))} if target is not None else {}


DEBT_RESERVE = ContractType(
    name="debt_reserve",
    terms=("covers", "periods_forward", "from", "prefund"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET))},
    flows=(FlowSpec("prefund", "fund", (Leg("holder", "{id}", "cash", (+1, -1)),)),
           FlowSpec("release", "pay", (Leg("holder", "cash", "{id}", (+1, -1)),))),
    amounts=_reserve_amounts,
    claim_specs=(ClaimSpec("top_up", "pay", Leg("holder", "{id}", "cash", (+1, -1))),),
    claims=_reserve_claims,
    refs=("covers",),
)


def _lc_amounts(terms, t, bal=None):
    target = _reserve_target(terms, t)
    return {"fee": target * exact(terms["fee_rate"]) * _days(terms)} if target else {}


RESERVE_LETTER_OF_CREDIT = ContractType(
    name="reserve_letter_of_credit",
    terms=("covers", "periods_forward", "from", "fee_rate", "days_per_period", "day_count_basis"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}.fee", Nature.EXPENSE), ("{id}.fee_payable", Nature.LIABILITY))},
    flows=(FlowSpec("fee", "accrue", (Leg("holder", "{id}.fee", "{id}.fee_payable", (+1, +1)),)),),
    amounts=_lc_amounts,
    claim_specs=(ClaimSpec("fee", "pay", Leg("holder", "{id}.fee_payable", "cash", (-1, -1))),),
    claims=lambda terms, t, bal: {"fee": bal("holder", "{id}.fee_payable")},
    refs=("covers",),
)


# Equity -----------------------------------------------------------------

SPONSOR_EQUITY = ContractType(
    name="sponsor_equity",
    terms=(),
    parties=("issuer",),
    accounts={"issuer": (("cash", Nature.ASSET), ("{id}.capital", Nature.EQUITY),
                         ("{id}.distributions", Nature.EQUITY))},
    flows=(),
    amounts=lambda terms, t, bal=None: {},
    legs={"contribute": Leg("issuer", "cash", "{id}.capital", (+1, +1)),
          "distribute": Leg("issuer", "{id}.distributions", "cash", (-1, -1))},
)

REGISTRY = {t.name: t for t in (EQUITY_FUNDING, ONE_OFF_PURCHASE, SHARED_SAVINGS, TERM_LOAN, SHAREHOLDER_LOAN,
                                OFFTAKE, OPERATING_COST, CAPEX, DEBT_RESERVE, RESERVE_LETTER_OF_CREDIT,
                                SPONSOR_EQUITY)}
