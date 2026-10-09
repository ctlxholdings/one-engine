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

Modelling note on ``shared_savings``: the client's savings are booked as an
income line ("avoided energy cost"), i.e. a with-versus-without view of the
client's utility bill. A lens that restates the client against its baseline
would read the same postings.
"""
from __future__ import annotations

from one_engine.contracts import ContractType, FlowSpec, Leg
from one_engine.kernel import Nature
from one_engine.units import exact


def _once(terms, t):
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


def _shared_savings(terms, t):
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

REGISTRY = {t.name: t for t in (EQUITY_FUNDING, ONE_OFF_PURCHASE, SHARED_SAVINGS)}
