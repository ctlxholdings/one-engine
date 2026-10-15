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
* ``sponsor_equity``    contributions, distributions and returns of
  capital as named legs whose amounts a funding plan, a waterfall or a
  promote decides.

Added for the 500MW nightmare (step 7):

* ``loan`` / ``foreign_loan`` the full facility: draw schedule, floating
  rate series, grace-period capitalisation, arrangement fee amortised,
  equal-step, bullet or sculpted repayment, a prepayment a waterfall may
  sweep; the foreign variant keeps its principal in its own currency and
  remeasures at each close;
* ``operating_cost`` gains an escalation ``index``;
* ``debt_reserve`` covers one loan or several, and its release is a claim
  the waterfall takes (it may depend on a sculpted loan's CFADS);
* ``maintenance_reserve`` builds a target to an event, then the event's
  cost is spent from it and capitalised;
* ``sinking_fund`` builds a target over a window; another rulebook pays
  out of it (decommissioning).

Changed for Bodmer's 6MW solar (step 7e), whose grid is nine months then
quarters:

* ``days_per_period`` and ``commitment_fee_rate`` may be per-period series
  (a month is 30/360, a quarter 90/360; the fee runs in construction only);
* ``loan`` / ``foreign_loan`` gain ``principal_schedule``: null, or the
  principal due each period (a sizing computes it). A negative amount is
  interest the schedule does not pay: it is capitalised.

Added for ModelOff 2013 "Hard Times" (step 7l), a monthly cash forecast:

* ``payment_days`` of ``offtake`` and ``operating_cost`` may be a profile:
  the shares of a period's amount paid in it and in each period after,
  summing to one (customers pay 60% a month later, 25% two, 15% three);
* ``receivable_run_off`` / ``payable_run_off`` settle a balance carried in
  on the opening balance sheet (the account is the contract's id) over a
  stated profile.

Added for ModelOff 2016 "Fund the Future" (step 7o), four savings accounts:

* ``deposit_account`` an account that earns a return on its opening
  balance (``rate`` x ``days_per_period`` / ``day_count_basis``, each a
  number or a series; the return is added to the balance, so it
  compounds), takes the deposits of ``schedule`` as claims, and is
  withdrawn in full at ``close_at`` (the deposit of that period included,
  when its withdrawal is settled in a phase after the deposit). Nothing is
  due after it closes. A days series of zeros but one period a year makes
  an annual account on a quarterly grid.

Added for ModelOff 2014 "Time is Money" (step 7p), two asset classes
depreciated differently:

* ``capex_class`` the ``capex`` contract with its spend held in the
  contract's own account (``{id}``) rather than the shared ``cwip``, so
  that each class has its own asset register.

Added for ModelOff 2017 "Collect the Cash" (step 7q), a daily cash forecast:

* ``offtake_on_dates`` a sale (volume x price) collected in full, with
  everything owed before it, on each listed collection period
  (``collect_on``), and not otherwise: a customer who accrues every day
  and pays on the third Thursday, or on the next weekday.

Modelling note on ``shared_savings``: the client's savings are booked as an
income line ("avoided energy cost"), i.e. a with-versus-without view of the
client's utility bill. A lens that restates the client against its baseline
would read the same postings.

Changed for the failure panel (W2 on ERIA): ``performance`` of
``shared_savings`` may be a series, one factor per year of the term. The
provider's fee stays ``split`` of the savings measured, so an
underperforming year costs both parties in proportion.
"""
from __future__ import annotations

from one_engine.contracts import ClaimSpec, ContractType, FlowSpec, Leg
# the loan terms' conventions, shared with the loop rules; the short names are the ones used throughout here
from one_engine.contracts import maturity as _maturity, year_fraction as _days
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
    # performance is one factor, or one per year of the term (indexed from ``start``): a ramp the
    # failure panel's W2 declares. The fee stays the split of what was measured
    performance = terms["performance"]
    if isinstance(performance, (list, tuple)):
        if len(performance) != n:
            raise ValueError(f"shared_savings: performance has {len(performance)} values for a term of {n}")
        performance = performance[t - start]
    savings = exact(terms["kwh"]) * exact(terms["tariff"]) * exact(performance)
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
    balance x rate x days_per_period / day_count_basis, from ``rate_from``
(the rate may be a per-period series: a floating base plus margin).
    A debt reserve is sized on this schedule, not on the actual balance (a
    reserve that shrank because a payment was missed would defeat its purpose).
    """
    def target(p):
        if p < int(terms["repay_from"]):
            return exact(terms["commitment"])
        step = exact(terms["commitment"]) / int(terms["repay_periods"])
        return max(exact(0), exact(terms["commitment"]) - step * (p - int(terms["repay_from"]) + 1))
    opening = target(t - 1) if t > 0 else exact(terms["commitment"])
    interest = opening * _at(terms["rate"], t) * _days(terms, t) if t >= int(terms["rate_from"]) else exact(0)
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
            out["interest"] = owed(bal, opening=True) * _at(terms["rate"], t) * _days(terms, t)
        if t <= int(terms["available_until"]):
            undrawn = exact(terms["commitment"]) - bal.opening("borrower", "{id}.debt")
            out["commitment_fee"] = undrawn * _at(terms["commitment_fee_rate"], t) * _days(terms, t)
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


# The full facility (nightmare case, step 7) ----------------------------------


def _capitalised(terms, t):
    return int(terms["capitalise_from"]) <= t <= int(terms["capitalise_until"])


def _facility(name, foreign):
    """A loan facility with every term the 500MW nightmare's four lenders use.

    One type covers a euro senior loan, a dollar loan in a euro book
    (``foreign``), a mezzanine with a grace period and a cash sweep, and a
    sculpted loan; terms are never optional, so each case states them all.

    Terms
    -----
    commitment, draw_schedule, available_until
        Drawn as a source claim, at most ``draw_schedule[t]`` and what is
        left of the commitment, in the loan's currency.
    rate, rate_from, days_per_period, day_count_basis
        Interest on the opening amount owed (principal plus capitalised
        interest); ``rate`` may be a series (floating base plus margin).
    commitment_fee_rate
        On the undrawn commitment while the facility is available.
    capitalise_from, capitalise_until
        Interest of the periods in this window (inclusive) is capitalised
        under the agreement (a grace period after COD): it is never due in
        cash. An empty window (``until < from``) means none. This is
        contractual PIK; a missed payment outside it stays due (arrears).
    repay_from, repay_periods
        Maturity is ``repay_from + repay_periods - 1``. With ``sculpt_dscr``
        null the balance falls in equal steps (one step is a bullet);
    sculpt_dscr
        otherwise principal due is ``CFADS / sculpt_dscr`` less the interest
        due, within what is owed, and whatever is left is due at maturity.
        CFADS is passed by the rulebook (``bal.given("cfads")``).
    principal_schedule
        null, or the principal due in each period from ``repay_from``
        (a series; a sizing computes it), within what is owed, the rest at
        maturity. A negative amount is interest the schedule leaves unpaid
        (debt service below interest, as when a sculpted size meets a weak
        quarter): that much of the period's interest is capitalised. One of
        ``sculpt_dscr`` and ``principal_schedule`` must be null.
    arrangement_fee, fee_at
        Paid once at ``fee_at`` (a claim, funded like any use), carried as
        a deferred cost and amortised straight-line to ``{id}.fees`` from
        the next period to maturity. Straight-line rather than effective
        interest: the old engine's convention, and the difference is below
        a basis point of the loan here (declared simplification).
    currency, fx_rate (``foreign`` only)
        The loan's currency and the functional units per unit of it, by
        period.

    Claims: ``draw`` (source), ``arrangement_fee``, ``interest``, ``fee``,
    ``capitalised`` (capitalised interest, repaid ahead of principal),
    ``principal``, and ``prepay`` (the rest of the principal, which a
    waterfall may sweep).

    Foreign currency
    ----------------
    Every amount is computed in the loan's currency and posted in the
    functional currency at the period's rate. The dollar amounts owed are
    kept in ``{id}.debt_fc`` and ``{id}.capitalised_interest_fc`` (accounts
    in the loan's currency, against ``{id}.fc_position``). At close the
    period's euro movements are converted at the same rate and posted to
    them. The remeasurement is made at the start of each period, in the
    accrue phase: the opening euro carrying amounts are brought to the
    opening dollar amounts at the period's rate, the difference to
    ``{id}.fx_loss`` (:func:`~one_engine.measurement.remeasure`). Every
    movement of the period is then made at that same rate, so the closing
    carrying amount is the closing dollar amount at the period's rate
    (tested every period), and the exchange result falls in the period's
    taxable base, which is computed before close. Payables accrued and
    paid in the same period need no remeasurement; arrears carried across
    a period are not remeasured (none arise in the cases so far). The
    accrue phase must therefore come before any phase in which the loan is
    drawn or repaid (it is the first phase of the nightmare case).
    """
    from one_engine.measurement import remeasure

    borrower = [("cash", Nature.ASSET), ("{id}.debt", Nature.LIABILITY),
                ("{id}.capitalised_interest", Nature.LIABILITY), ("{id}.interest", Nature.EXPENSE),
                ("{id}.interest_payable", Nature.LIABILITY), ("{id}.fees", Nature.EXPENSE),
                ("{id}.fee_payable", Nature.LIABILITY), ("{id}.deferred_fees", Nature.ASSET)]
    if foreign:
        borrower += [("{id}.fx_loss", Nature.EXPENSE),
                     ("{id}.debt_fc", Nature.LIABILITY, "currency"),
                     ("{id}.capitalised_interest_fc", Nature.LIABILITY, "currency"),
                     ("{id}.fc_position", Nature.BOUNDARY, "currency")]
    pairs = (("{id}.debt", "{id}.debt_fc"), ("{id}.capitalised_interest", "{id}.capitalised_interest_fc"))

    def fx(terms, t):
        return _at(terms["fx_rate"], t) if foreign else exact(1)

    def fc_now(terms, bal, acct, t):
        """Amount owed on ``acct`` so far in ``t``, in the loan's currency.

        Once the opening balance is remeasured (accrue phase) every euro on the account is at
        this period's rate, so the dollar amount is the carrying amount over the rate.
        """
        return bal("borrower", acct) / fx(terms, t) if foreign else bal("borrower", acct)

    def fc_opening(terms, bal, acct):
        return bal.opening("borrower", dict(pairs)[acct] if foreign else acct)

    def interest_due(terms, t, owed_fc):
        if t < int(terms["rate_from"]):
            return exact(0)
        return owed_fc * _at(terms["rate"], t) * _days(terms, t) * fx(terms, t)

    def scheduled(terms, t):
        """The principal the schedule sets for ``t`` (functional currency), or None (no schedule, not yet due)."""
        if terms["principal_schedule"] is None or not int(terms["repay_from"]) <= t < _maturity(terms):
            return None
        return _at(terms["principal_schedule"], t) * fx(terms, t)

    def amounts(terms, t, bal=None):
        if bal is None:
            return {}
        if terms["sculpt_dscr"] is not None and terms["principal_schedule"] is not None:
            raise ValueError("a loan is sculpted or follows a principal schedule, not both")
        out = {}
        owed_open = sum((fc_opening(terms, bal, a) for a, _ in pairs), exact(0))
        interest = interest_due(terms, t, owed_open)
        out["pik" if _capitalised(terms, t) else "interest"] = interest
        s = scheduled(terms, t)
        if s is not None and s < 0 and not _capitalised(terms, t):
            # debt service below interest: the part of the interest the schedule leaves unpaid is capitalised
            if -s > interest:
                raise ValueError(f"period {t}: a scheduled repayment of {s} is below minus the interest "
                                 f"({interest}); the difference would be a new draw")
            out["pik"], out["interest"] = -s, interest + s
        if t <= int(terms["available_until"]):
            undrawn = exact(terms["commitment"]) - fc_opening(terms, bal, "{id}.debt")
            out["commitment_fee"] = (undrawn * _at(terms["commitment_fee_rate"], t) * _days(terms, t)
                                     * fx(terms, t))
        if int(terms["fee_at"]) < t <= _maturity(terms):
            out["amortise_fee"] = bal.opening("borrower", "{id}.deferred_fees") / (_maturity(terms) - t + 1)
        if foreign:
            for acct, twin in pairs:
                key = "debt" if acct == "{id}.debt" else "capitalised"
                # the opening balance is remeasured to this period's rate first (so the result is in
                # this period's taxable base); every movement of the period is then made at that rate
                adj = remeasure(bal.opening("borrower", twin), fx(terms, t), bal.opening("borrower", acct))
                out[f"{key}_fx_loss"], out[f"{key}_fx_gain"] = max(adj, exact(0)), max(-adj, exact(0))
                move = bal("borrower", acct) - bal.opening("borrower", acct) - adj
                twin_move = move / fx(terms, t)
                out[f"{key}_fc_up"], out[f"{key}_fc_down"] = max(twin_move, exact(0)), max(-twin_move, exact(0))
        return out

    def claims(terms, t, bal):
        rate = fx(terms, t)
        due = {"interest": bal("borrower", "{id}.interest_payable"), "fee": bal("borrower", "{id}.fee_payable")}
        if t <= int(terms["available_until"]):
            room = exact(terms["commitment"]) - fc_now(terms, bal, "{id}.debt", t)
            due["draw"] = max(exact(0), min(room, _at(terms["draw_schedule"], t))) * rate
        if t == int(terms["fee_at"]):
            due["arrangement_fee"] = max(exact(0), exact(terms["arrangement_fee"]) * rate
                                         - bal("borrower", "{id}.deferred_fees"))
        # what is owed now, at this period's rate: a dollar loan is repaid in dollars, so paying its
        # euro carrying amount (measured at last period's rate) would leave a sliver of dollars owed
        debt = fc_now(terms, bal, "{id}.debt", t) * rate
        cap = fc_now(terms, bal, "{id}.capitalised_interest", t) * rate
        excess = exact(0)
        if t >= int(terms["repay_from"]):
            if t < _maturity(terms) and terms["principal_schedule"] is not None:
                excess = min(debt + cap, max(exact(0), scheduled(terms, t)))
            elif terms["sculpt_dscr"] is None and terms["principal_schedule"] is None:
                _, _, target = loan_schedule(terms, t)
                excess = max(exact(0), (debt + cap) / rate - target) * rate
            elif t >= _maturity(terms):
                excess = debt + cap
            else:
                sculpted = bal.given("cfads") / exact(terms["sculpt_dscr"]) - due["interest"]
                excess = min(debt + cap, max(exact(0), sculpted))
        due["capitalised"] = min(cap, excess)
        due["principal"] = excess - due["capitalised"]
        if t > int(terms["available_until"]):
            due["prepay"] = debt - due["principal"]
        return due

    flows = [FlowSpec("interest", "accrue", (Leg("borrower", "{id}.interest", "{id}.interest_payable", (+1, +1)),)),
             FlowSpec("pik", "accrue", (Leg("borrower", "{id}.interest", "{id}.capitalised_interest", (+1, +1)),)),
             FlowSpec("commitment_fee", "accrue", (Leg("borrower", "{id}.fees", "{id}.fee_payable", (+1, +1)),)),
             FlowSpec("amortise_fee", "accrue", (Leg("borrower", "{id}.fees", "{id}.deferred_fees", (+1, -1)),))]
    if foreign:
        for key, (acct, twin) in zip(("debt", "capitalised"), pairs):
            flows += [FlowSpec(f"{key}_fx_loss", "accrue", (Leg("borrower", "{id}.fx_loss", acct, (+1, +1)),)),
                      FlowSpec(f"{key}_fx_gain", "accrue", (Leg("borrower", acct, "{id}.fx_loss", (-1, -1)),)),
                      FlowSpec(f"{key}_fc_up", "close", (Leg("borrower", "{id}.fc_position", twin, (-1, +1)),)),
                      FlowSpec(f"{key}_fc_down", "close", (Leg("borrower", twin, "{id}.fc_position", (-1, +1)),))]
    return ContractType(
        name=name,
        terms=(("currency", "fx_rate") if foreign else ()) + (
            "commitment", "draw_schedule", "available_until", "rate", "rate_from", "days_per_period",
            "day_count_basis", "commitment_fee_rate", "capitalise_from", "capitalise_until", "repay_from", "repay_periods",
            "sculpt_dscr", "principal_schedule", "arrangement_fee", "fee_at"),
        parties=("borrower",),
        accounts={"borrower": tuple(borrower)},
        flows=tuple(flows),
        amounts=amounts,
        claim_specs=(
            ClaimSpec("draw", "source", Leg("borrower", "cash", "{id}.debt", (+1, +1))),
            ClaimSpec("arrangement_fee", "pay", Leg("borrower", "{id}.deferred_fees", "cash", (+1, -1))),
            ClaimSpec("interest", "pay", Leg("borrower", "{id}.interest_payable", "cash", (-1, -1))),
            ClaimSpec("fee", "pay", Leg("borrower", "{id}.fee_payable", "cash", (-1, -1))),
            ClaimSpec("capitalised", "pay", Leg("borrower", "{id}.capitalised_interest", "cash", (-1, -1))),
            ClaimSpec("principal", "pay", Leg("borrower", "{id}.debt", "cash", (-1, -1))),
            ClaimSpec("prepay", "pay", Leg("borrower", "{id}.debt", "cash", (-1, -1)))),
        claims=claims,
    )


LOAN = _facility("loan", foreign=False)
FOREIGN_LOAN = _facility("foreign_loan", foreign=True)


def forward_service(loan, p, bal):
    """Debt service of ``loan`` expected in a future period ``p``, in functional currency.

    For a reserve sized on the loans it covers. A loan on a fixed schedule
    gives its scheduled service (never its actual balance: a reserve that
    shrank because a payment was missed would defeat its purpose). A
    sculpted loan has no schedule; its expected service is its current
    period's, CFADS / target within what is owed (declared estimate; a
    balance left for maturity is not reserved for). Nothing is expected
    after maturity.
    """
    terms = loan.values()
    if loan.type.name in ("term_loan", "shareholder_loan"):
        return sum(loan_schedule(terms, p)[:2], exact(0))
    if p > _maturity(terms):
        return exact(0)
    if terms["principal_schedule"] is not None:
        raise NotImplementedError(f"a reserve on '{loan.id}', a loan on a principal schedule, is not built")
    lb = bal.of(loan)
    rate = _at(terms["fx_rate"], bal.period) if loan.type.name == "foreign_loan" else exact(1)
    owed = lb("borrower", "{id}.debt") + lb("borrower", "{id}.capitalised_interest")
    interest = (owed * _at(terms["rate"], p) * _days(terms, p)
                if not _capitalised(terms, p) and p >= int(terms["rate_from"]) else exact(0))
    if p < int(terms["repay_from"]):
        return interest
    if terms["sculpt_dscr"] is None:
        principal, sched_interest, _ = loan_schedule(terms, p)
        return (principal + (0 if _capitalised(terms, p) else sched_interest)) * rate
    return min(owed + interest, max(interest, lb.given("cfads") / exact(terms["sculpt_dscr"])))


# Sales and costs on payment terms ----------------------------------------

def _profile(shares, where):
    """A payment profile: the shares of an amount paid in its period and each one after, summing to one."""
    out = [exact(x) for x in shares]
    if any(x < 0 for x in out) or sum(out) != 1:
        raise ValueError(f"{where}: a payment profile's shares must be non-negative and sum to 1, "
                         f"got {[float(x) for x in out]}")
    return out


def _terms_target(amount, terms, t, amount_at=None):
    """Balance left unpaid at period end under ``payment_days`` terms (zero from ``end``).

    Annualised flow x payment_days / days_per_year: the target-balance
    method on a period grid that cannot see individual invoices.

    ``payment_days`` may instead be a profile (ModelOff 2013 "Hard Times",
    step 7l): the shares of a period's amount paid in that period and in
    each period after it, summing to one (``[0, 0.6, 0.25, 0.15]``: nothing
    in the month, 60% the next month, ...). The target is then what is
    still unpaid of each recent period's amount, ``amount_at(t - k)`` times
    the share not yet paid ``k`` periods on.
    """
    if t >= int(terms["end"]):
        return exact(0)
    days = terms["payment_days"]
    if isinstance(days, (list, tuple)):
        left, unpaid = exact(1), exact(0)
        for k, share in enumerate(_profile(days, "payment_days")):
            left -= share
            if t - k >= 0:
                unpaid += amount_at(t - k) * left
        return unpaid
    return amount * int(terms["periods_per_year"]) * exact(days) / exact(terms["days_per_year"])


def _sale(terms, t):
    return _at(terms["volume"], t) * _at(terms["price"], t) if t <= int(terms["end"]) else exact(0)


def _offtake(terms, t, bal=None):
    sale = _sale(terms, t)
    out = {"sale": sale}
    if bal is not None:
        out["collect"] = (bal("seller", "{id}.receivable") + sale
                          - _terms_target(sale, terms, t, lambda p: _sale(terms, p)))
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


def _offtake_on_dates(terms, t, bal=None):
    """A sale collected in full, with everything owed before it, on each of the listed collection periods."""
    sale = _sale(terms, t)
    out = {"sale": sale}
    if bal is not None:
        on = {int(x) for x in terms["collect_on"]}
        out["collect"] = bal("seller", "{id}.receivable") + sale if t in on else exact(0)
    return out


# a sale that accrues day by day and is paid on stated days (the third Thursday of each month: ModelOff 2017
# "Collect the Cash", step 7q); payment_days cannot say this, since its target is a share of each period's sale
OFFTAKE_ON_DATES = ContractType(
    name="offtake_on_dates",
    terms=("volume", "price", "collect_on", "end"),
    parties=("seller",),
    accounts=OFFTAKE.accounts,
    flows=OFFTAKE.flows,
    amounts=_offtake_on_dates,
)


def _cost_base(terms, t):
    """Fixed annual cost per period plus rate x volume, both times the escalation ``index`` of ``t``."""
    if not int(terms["start"]) <= t <= int(terms["end"]):
        return exact(0)
    return _at(terms["index"], t) * (exact(terms["annual"]) / int(terms["periods_per_year"])
                                     + exact(terms["unit_rate"]) * _at(terms["volume"], t))


def _operating_cost(terms, t, bal=None):
    cost = _cost_base(terms, t)
    vat = exact(terms["vat_rate"]) * cost
    out = {"cost": cost, "vat": vat, "vat_refund": _vat_refund(terms, t, lambda p: _cost_base(terms, p))}
    if bal is not None:
        gross = (lambda p: _cost_base(terms, p) * (1 + exact(terms["vat_rate"])))
        out["payment"] = bal("payer", "{id}.payable") + cost + vat - _terms_target(cost + vat, terms, t, gross)
    return out


OPERATING_COST = ContractType(
    name="operating_cost",
    terms=("annual", "unit_rate", "volume", "index", "periods_per_year", "start", "end", "vat_rate",
           "vat_refund_lag", "payment_days", "days_per_year"),
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


def _capex_type(name, cwip):
    """A construction spend schedule into ``cwip`` (the shared account, or the contract's own)."""
    return ContractType(
        name=name,
        terms=("schedule", "vat_rate", "vat_refund_lag", "end"),
        parties=("owner",),
        accounts={"owner": (("cash", Nature.ASSET), (cwip, Nature.ASSET), ("vat_receivable", Nature.ASSET))},
        flows=(FlowSpec("spend", "fund", (Leg("owner", cwip, "cash", (+1, -1)),)),
               FlowSpec("vat", "fund", (Leg("owner", "vat_receivable", "cash", (+1, -1)),)),
               FlowSpec("vat_refund", "operate", (Leg("owner", "cash", "vat_receivable", (+1, -1)),))),
        amounts=_capex,
    )


CAPEX = _capex_type("capex", "cwip")
# an asset class's capex, held in its own account (``{id}``) so that each class can have its own register
CAPEX_CLASS = _capex_type("capex_class", "{id}")


# Balances carried in --------------------------------------------------

def _run_off(terms, t, bal=None):
    """This period's share of a balance carried in on the opening balance sheet.

    What is left of the balance, over the shares of the profile not yet
    reached, times this period's share: the balance is settled exactly on
    the profile, and nothing else needs to know its opening amount.
    """
    shares = _profile(terms["profile"], "run_off.profile")
    if bal is None or t >= len(shares):
        return {}
    left = 1 - sum(shares[:t], exact(0))
    return {"settle": bal.opening("holder", "{id}") * shares[t] / left} if left else {}


RECEIVABLE_RUN_OFF = ContractType(
    name="receivable_run_off",
    terms=("profile",),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET))},
    flows=(FlowSpec("settle", "operate", (Leg("holder", "cash", "{id}", (+1, -1)),)),),
    amounts=_run_off,
)

PAYABLE_RUN_OFF = ContractType(
    name="payable_run_off",
    terms=("profile",),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.LIABILITY))},
    flows=(FlowSpec("settle", "operate", (Leg("holder", "{id}", "cash", (-1, -1)),)),),
    amounts=_run_off,
)


# Reserves ---------------------------------------------------------------

def _loans(terms):
    covers = terms["covers"]
    return list(covers) if isinstance(covers, (list, tuple)) else [covers]


def _reserve_target(terms, t, bal):
    """The expected debt service of the covered loans over the next ``periods_forward`` periods."""
    if t < int(terms["from"]):
        return None
    return sum((forward_service(loan, p, bal) for loan in _loans(terms)
                for p in range(t + 1, t + 1 + int(terms["periods_forward"]))), exact(0))


def _reserve_amounts(terms, t, bal=None):
    return {"prefund": _at(terms["prefund"], t)} if t < len(terms["prefund"]) else {}


def _reserve_claims(terms, t, bal):
    target = _reserve_target(terms, t, bal)
    if target is None:
        return {}
    held = bal("holder", "{id}")
    return {"top_up": max(exact(0), target - held), "release": max(exact(0), held - target)}


DEBT_RESERVE = ContractType(
    name="debt_reserve",
    terms=("covers", "periods_forward", "from", "prefund"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET))},
    flows=(FlowSpec("prefund", "fund", (Leg("holder", "{id}", "cash", (+1, -1)),)),),
    amounts=_reserve_amounts,
    claim_specs=(ClaimSpec("top_up", "pay", Leg("holder", "{id}", "cash", (+1, -1))),
                 ClaimSpec("release", "source", Leg("holder", "cash", "{id}", (+1, -1)))),
    claims=_reserve_claims,
    refs=("covers",),
)


def _lc_amounts(terms, t, bal=None):
    target = _reserve_target(terms, t, bal) if bal is not None else None
    return {"fee": target * exact(terms["fee_rate"]) * _days(terms, t)} if target else {}


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


def _maintenance_amounts(terms, t, bal=None):
    if bal is None or t != int(terms["event"]):
        return {}
    cost = exact(terms["amount"])
    from_reserve = min(cost, bal("holder", "{id}"))
    return {"spend": from_reserve, "spend_cash": cost - from_reserve}


def _maintenance_claims(terms, t, bal):
    start, event = int(terms["from"]), int(terms["event"])
    if not start <= t < event:
        return {}
    target = exact(terms["amount"]) * (t - start + 1) / (event - start)
    return {"contribute": max(exact(0), target - bal("holder", "{id}"))}


def _sinking_claims(terms, t, bal):
    start, until = int(terms["from"]), int(terms["until"])
    if not start <= t <= until:
        return {}
    target = exact(terms["amount"]) * (t - start + 1) / (until - start + 1)
    return {"contribute": max(exact(0), target - bal("holder", "{id}"))}


SINKING_FUND = ContractType(
    name="sinking_fund",
    terms=("amount", "from", "until"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET))},
    flows=(),
    amounts=lambda terms, t, bal=None: {},
    claim_specs=(ClaimSpec("contribute", "pay", Leg("holder", "{id}", "cash", (+1, -1))),),
    claims=_sinking_claims,
)

def _deposit_amounts(terms, t, bal=None):
    """The period's return: the opening balance x rate x days_per_period / day_count_basis."""
    if bal is None:
        return {}
    return {"return": bal.opening("holder", "{id}") * _at(terms["rate"], t) * _days(terms, t)}


def _deposit_claims(terms, t, bal):
    close = int(terms["close_at"])
    if t > close:
        return {}
    due = {"deposit": _at(terms["schedule"], t)}
    if t == close:
        due["withdraw"] = bal("holder", "{id}")
    return due


DEPOSIT_ACCOUNT = ContractType(
    name="deposit_account",
    terms=("schedule", "rate", "days_per_period", "day_count_basis", "close_at"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET), ("{id}.return", Nature.INCOME))},
    flows=(FlowSpec("return", "accrue", (Leg("holder", "{id}", "{id}.return", (+1, +1)),)),),
    amounts=_deposit_amounts,
    claim_specs=(ClaimSpec("deposit", "pay", Leg("holder", "{id}", "cash", (+1, -1))),
                 ClaimSpec("withdraw", "source", Leg("holder", "cash", "{id}", (+1, -1)))),
    claims=_deposit_claims,
)

MAINTENANCE_RESERVE = ContractType(
    name="maintenance_reserve",
    terms=("amount", "from", "event"),
    parties=("holder",),
    accounts={"holder": (("cash", Nature.ASSET), ("{id}", Nature.ASSET), ("cwip", Nature.ASSET))},
    flows=(FlowSpec("spend", "accrue", (Leg("holder", "cwip", "{id}", (+1, -1)),)),
           FlowSpec("spend_cash", "accrue", (Leg("holder", "cwip", "cash", (+1, -1)),))),
    amounts=_maintenance_amounts,
    claim_specs=(ClaimSpec("contribute", "pay", Leg("holder", "{id}", "cash", (+1, -1))),),
    claims=_maintenance_claims,
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
          "distribute": Leg("issuer", "{id}.distributions", "cash", (-1, -1)),
          "return_capital": Leg("issuer", "{id}.capital", "cash", (-1, -1))},
)

REGISTRY = {t.name: t for t in (EQUITY_FUNDING, ONE_OFF_PURCHASE, SHARED_SAVINGS, TERM_LOAN, SHAREHOLDER_LOAN,
                                LOAN, FOREIGN_LOAN, OFFTAKE, OPERATING_COST, CAPEX, DEBT_RESERVE,
                                RESERVE_LETTER_OF_CREDIT, MAINTENANCE_RESERVE, SINKING_FUND, SPONSOR_EQUITY,
                                RECEIVABLE_RUN_OFF, PAYABLE_RUN_OFF, DEPOSIT_ACCOUNT, CAPEX_CLASS,
                                OFFTAKE_ON_DATES)}
