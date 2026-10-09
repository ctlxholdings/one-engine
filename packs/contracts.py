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

Added for Averta's 100MW SPV (plan step 6):

* ``term_loan``         drawn as a source, interest accrued on the opening
  balance, interest and principal claimed each period; an unpaid amount
  stays due (arrears);
* ``shareholder_loan``  the same, except that unpaid interest is
  capitalised at period close (PIK), so it is repaid through the principal
  claim, which reads the balance;
* ``offtake``           volume x price into revenue;
* ``fixed_cost``        an annual cost paid per period from a start period;
* ``capex``             a construction spend schedule into CWIP;
* ``debt_reserve``      a reserve sized on a loan's next scheduled debt
  service: prefunded during construction, topped up as a claim, released
  as a flow when above target;
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

# ── project finance (Averta 100MW SPV) ─────────────────────────────────────

def _at(x, t):
    """A term that is a number or a per-period series, at period ``t``."""
    if isinstance(x, (list, tuple)):
        if t >= len(x):
            raise ValueError(f"series of {len(x)} values has no period {t}")
        return exact(x[t])
    return exact(x)


def _year_fraction(terms):
    return exact(terms["rate"]) * exact(terms["days_per_period"]) / exact(terms["day_count_basis"])


def loan_schedule(terms, t):
    """Scheduled (principal, interest) of a loan in period ``t``, assuming full draw.

    The scheduled balance is the commitment until ``repay_from``, then falls
    by ``repay_amount`` a period to zero. Interest is the scheduled opening
    balance x rate x days_per_period / day_count_basis, from ``rate_from``. A debt reserve
    is sized on this schedule, not on the actual balance (a reserve that
    shrank because a payment was missed would defeat its purpose).
    """
    def target(p):
        if p < int(terms["repay_from"]):
            return exact(terms["commitment"])
        return max(exact(0), exact(terms["commitment"]) - exact(terms["repay_amount"]) * (p - int(terms["repay_from"]) + 1))
    opening = target(t - 1) if t > 0 else exact(terms["commitment"])
    interest = opening * _year_fraction(terms) if t >= int(terms["rate_from"]) else exact(0)
    return opening - target(t), interest, target(t)


def _loan_amounts(terms, t, bal=None):
    if bal is None or t < int(terms["rate_from"]):
        return {}
    return {"interest": bal.opening("borrower", "{id}.debt") * _year_fraction(terms)}


def _loan_claims(terms, t, bal):
    debt = bal("borrower", "{id}.debt")
    _, _, target = loan_schedule(terms, t)
    draw = max(exact(0), exact(terms["commitment"]) - debt) if t <= int(terms["available_until"]) else exact(0)
    return {"draw": draw,
            "interest": bal("borrower", "{id}.interest_payable"),
            "principal": max(exact(0), debt - target) if t >= int(terms["repay_from"]) else exact(0)}


def _loan(name, unpaid_interest):
    borrower = (("cash", Nature.ASSET), ("{id}.debt", Nature.LIABILITY),
                ("{id}.interest", Nature.EXPENSE), ("{id}.interest_payable", Nature.LIABILITY))
    pik = Leg("borrower", "{id}.interest_payable", "{id}.debt", (-1, +1)) if unpaid_interest == "capitalise" else None
    return ContractType(
        name=name,
        terms=("commitment", "rate", "rate_from", "days_per_period", "day_count_basis", "available_until",
               "repay_from", "repay_amount"),
        parties=("borrower",),
        accounts={"borrower": borrower},
        flows=(FlowSpec("interest", "accrue", (Leg("borrower", "{id}.interest", "{id}.interest_payable", (+1, +1)),)),),
        amounts=_loan_amounts,
        claim_specs=(
            ClaimSpec("draw", "source", Leg("borrower", "cash", "{id}.debt", (+1, +1))),
            ClaimSpec("interest", "pay", Leg("borrower", "{id}.interest_payable", "cash", (-1, -1)), shortfall=pik),
            ClaimSpec("principal", "pay", Leg("borrower", "{id}.debt", "cash", (-1, -1))),
        ),
        claims=_loan_claims,
    )


TERM_LOAN = _loan("term_loan", unpaid_interest="arrears")
SHAREHOLDER_LOAN = _loan("shareholder_loan", unpaid_interest="capitalise")

OFFTAKE = ContractType(
    name="offtake",
    terms=("volume", "price"),
    parties=("seller",),
    accounts={"seller": (("cash", Nature.ASSET), ("{id}.revenue", Nature.INCOME))},
    flows=(FlowSpec("sale", "operate", (Leg("seller", "cash", "{id}.revenue", (+1, +1)),)),),
    amounts=lambda terms, t, bal=None: {"sale": _at(terms["volume"], t) * _at(terms["price"], t)},
)


def _fixed_cost(terms, t, bal=None):
    if t < int(terms["start"]):
        return {}
    return {"cost": exact(terms["annual"]) / int(terms["periods_per_year"])}


FIXED_COST = ContractType(
    name="fixed_cost",
    terms=("annual", "periods_per_year", "start"),
    parties=("payer",),
    accounts={"payer": (("cash", Nature.ASSET), ("{id}.expense", Nature.EXPENSE))},
    flows=(FlowSpec("cost", "operate", (Leg("payer", "{id}.expense", "cash", (+1, -1)),)),),
    amounts=_fixed_cost,
)

CAPEX = ContractType(
    name="capex",
    terms=("schedule",),
    parties=("owner",),
    accounts={"owner": (("cash", Nature.ASSET), ("cwip", Nature.ASSET))},
    flows=(FlowSpec("spend", "fund", (Leg("owner", "cwip", "cash", (+1, -1)),)),),
    amounts=lambda terms, t, bal=None: {"spend": _at(terms["schedule"], t)} if t < len(terms["schedule"]) else {},
)


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
                                OFFTAKE, FIXED_COST, CAPEX, DEBT_RESERVE, SPONSOR_EQUITY)}
