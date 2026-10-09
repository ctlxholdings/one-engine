"""ACTUS loan contracts: PAM, LAM, NAM and ANN on the ledger kernel (golds loan case, step 7).

What this pack is
-----------------
The golds loan cases are loans written in the ACTUS standard: a closed
vocabulary of contract terms, from which the standard derives a dated
stream of events (initial exchange, interest payments, principal
redemptions, rate resets...) and, per event, a payoff and a new contract
state. The ACTUS Financial Research Foundation publishes the terms *and*
the expected event stream for 109 such loans (``cases/reference/actus``,
CC BY-SA 4.0). That corpus is the referee: we authored no number on the
answer side.

How it sits on the engine
-------------------------
* **Clock.** One period per distinct event instant; phases are the ACTUS
  event types in the standard's same-day order (``PHASES``), read off the
  corpus itself. The calendar module derives every date from the terms:
  anchors, cycles with their stub rule, end of month, business days, and
  the day count each accrual uses.
* **State lives in the kernel.** Notional and accrued interest are money
  accounts in the holder's book. The rate, the next instalment, the
  interest base and the two scaling multipliers are state variables kept as
  stocks in units of their own (``FixedUnit``) against a boundary account,
  so every reset has a posting and an audit trail like any balance.
* **Direction follows nature.** Amounts are natural (unsigned) and the
  holder's role picks the natures: a lender (RPA) holds the notional and
  the accrued interest as assets and earns interest income; a borrower
  (RPL) owes them as liabilities and pays interest expense. The legs of a
  borrower's flow are the lender's with debit and credit swapped. ACTUS's
  signed state is the natural balance times the role sign. A quantity that
  can go either way (interest at a negative rate, a negative amortisation)
  has a mirrored flow, suffix ``~``.
* **Market data.** Rate resets and scaling read the reference series that
  ships with each contract (``dataObserved``) through
  :mod:`one_engine.market`, under its default exact-date rule: every reset
  in the corpus has an observation dated on the reset itself.
* **A purchase is a sale between two books.** For a contract bought
  mid-life (``purchaseDate``) the standard evolves the state from the
  status date and reports only from the purchase. Here the contract first
  runs in the seller's book up to the purchase; the buyer's book then
  starts from that state, and the purchase event pays the stated price plus
  accrued interest for it.

Measurement choices (the standard stops at the payoff)
------------------------------------------------------
A premium or discount at initial exchange, the gap between a purchase or
termination price and the notional, and the scaling effect on a payment
are recognised in a ``result`` account when they occur, not amortised.
The payoffs do not depend on this.

Rules the corpus decided (each pinned in ``tests/test_case_actus.py``)
---------------------------------------------------------------------
* events on the purchase date are the seller's (lam02);
* an annuity fixed before redemptions start is solved from the start of the
  first redemption period on the notional alone, counting a redemption on
  that start date (ann07, ann09); later it is solved from the event on
  notional plus accrued interest;
* a reset reads the observation on the reset date even when ``fixingDays``
  is two days (ann16). ``fixingDays`` is therefore read and has no effect;
* a borrower's accrued interest enters against a purchase or termination
  price (``_borrower_gap``; nam04, lam21);
* a stated reporting horizon (``to``) cuts the report, not the contract
  (ann08);
* with no maturity, a LAM matures at its ceil(NT / PRNXT)-th redemption and
  a NAM or ANN at the instalment that clears the notional after interest.

What refuses
------------
A contract type, role, term or convention this pack does not build stops
the run by name (``ActusError``): a term outside the closed vocabulary, a
known term we do not compute (fees, prepayment, caps), a business-day
convention or day count the calendar does not build.
"""
from __future__ import annotations

import datetime as _dt
import math
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import Any, Dict, List, Mapping, Optional, Tuple

from one_engine import dates as cal
from one_engine.contracts import Contract, ContractType, FixedUnit, FlowSpec, Leg
from one_engine.entities import Group
from one_engine.inputs import State, Value
from one_engine.kernel import Nature
from one_engine.market import Market, Series
from one_engine.units import exact


class ActusError(ValueError):
    """A contract uses something this pack does not build, or its terms do not fit together."""


#: Same-day order of event types, as the corpus publishes them (IED before PR
#: before IP before IPCI before RR ...). ``init`` posts the starting state.
PHASES = ("init", "PRD", "IED", "PR", "IP", "IPCI", "RR", "RRF", "PRF", "SC", "IPCB", "TD", "MD")

TYPES = ("PAM", "LAM", "NAM", "ANN")
ROLES = {"RPA": 1, "RPL": -1}

#: Terms read. Housekeeping terms (identifier, deal date) are read and have no effect on the stream.
BUILT_TERMS = frozenset({
    "contractType", "contractID", "contractRole", "contractDealDate", "statusDate", "currency",
    "notionalPrincipal", "initialExchangeDate", "maturityDate", "nominalInterestRate",
    "cycleAnchorDateOfInterestPayment", "cycleOfInterestPayment", "dayCountConvention",
    "endOfMonthConvention", "businessDayConvention", "calendar", "premiumDiscountAtIED",
    "accruedInterest", "capitalizationEndDate",
    "cycleAnchorDateOfRateReset", "cycleOfRateReset", "rateMultiplier", "rateSpread",
    "marketObjectCodeOfRateReset", "fixingDays", "nextResetRate",
    "purchaseDate", "priceAtPurchaseDate", "terminationDate", "priceAtTerminationDate",
    "cycleAnchorDateOfPrincipalRedemption", "cycleOfPrincipalRedemption",
    "nextPrincipalRedemptionPayment", "amortizationDate",
    "interestCalculationBase", "cycleAnchorDateOfInterestCalculationBase",
    "cycleOfInterestCalculationBase", "interestCalculationBaseAmount",
    "marketObjectCodeOfScalingIndex", "scalingIndexAtContractDealDate", "notionalScalingMultiplier",
    "interestScalingMultiplier", "cycleAnchorDateOfScalingIndex", "cycleOfScalingIndex",
    "scalingEffect",
})

#: ACTUS terms we know and do not compute; each refuses by name.
KNOWN_UNBUILT = frozenset({
    "feeRate", "feeBasis", "feeAccrued", "cycleOfFee", "cycleAnchorDateOfFee",
    "prepaymentEffect", "prepaymentPeriod", "optionExerciseEndDate", "cycleOfOptionality",
    "cycleAnchorDateOfOptionality", "penaltyType", "penaltyRate",
    "lifeCap", "lifeFloor", "periodCap", "periodFloor", "cyclePointOfRateReset",
    "cyclePointOfInterestPayment", "delinquencyPeriod", "gracePeriod", "creditLineAmount",
    "marketValueObserved", "settlementCurrency", "x_day_notice", "arrayCycleOfInterestPayment",
    "contractPerformance", "seniority", "nonPerformingDate",
})


# ── terms ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Terms:
    """The ACTUS terms of one contract, parsed: dates as instants, amounts exact, natural (unsigned)."""

    kind: str
    cid: str
    role: int
    currency: str
    sd: _dt.datetime
    ied: _dt.datetime
    md: Optional[_dt.datetime]
    nt: Fraction
    ipnr: Fraction
    dcc: str
    eomc: str
    bdc: Optional[str]
    cal: Optional[str]
    ipanx: Optional[_dt.datetime]
    ipcl: Optional[str]
    pdied: Fraction
    ipac: Optional[Fraction]
    ipced: Optional[_dt.datetime]
    rranx: Optional[_dt.datetime]
    rrcl: Optional[str]
    rrmlt: Fraction
    rrsp: Fraction
    rrmo: Optional[str]
    fixing: _dt.timedelta
    rrnxt: Optional[Fraction]
    prd: Optional[_dt.datetime]
    pprd: Fraction
    td: Optional[_dt.datetime]
    ptd: Fraction
    pranx: Optional[_dt.datetime]
    prcl: Optional[str]
    prnxt: Optional[Fraction]
    amd: Optional[_dt.datetime]
    ipcb: str
    ipcbanx: Optional[_dt.datetime]
    ipcbcl: Optional[str]
    ipcba: Optional[Fraction]
    scmo: Optional[str]
    scied: Optional[Fraction]
    scanx: Optional[_dt.datetime]
    sccl: Optional[str]
    scef: Optional[str]


def _num(v: Any) -> Fraction:
    return exact(str(v).strip())


def parse_terms(raw: Mapping[str, Any]) -> Terms:
    """Read an ACTUS term dictionary; refuse anything outside the built vocabulary by name."""
    unknown = sorted(set(raw) - BUILT_TERMS)
    unbuilt = [k for k in unknown if k in KNOWN_UNBUILT]
    if unbuilt:
        raise ActusError(f"{raw.get('contractID')}: term(s) {unbuilt} are ACTUS terms this pack does not compute")
    if unknown:
        raise ActusError(f"{raw.get('contractID')}: unknown term(s) {unknown}")
    kind = raw["contractType"]
    if kind not in TYPES:
        raise ActusError(f"{raw.get('contractID')}: contract type {kind!r} is not built ({TYPES})")
    if raw["contractRole"] not in ROLES:
        raise ActusError(f"{raw.get('contractID')}: role {raw['contractRole']!r} is not built ({sorted(ROLES)})")

    def d(k):
        return cal.instant(raw[k]) if raw.get(k) not in (None, "") else None

    def n(k, default=None):
        return _num(raw[k]) if raw.get(k) not in (None, "") else default

    def s(k, default=None):
        v = raw.get(k)
        return str(v).strip() if v not in (None, "") else default

    fixing = s("fixingDays", "P0D")
    if not (fixing.startswith("P") and fixing.endswith("D") and fixing[1:-1].isdigit()):
        raise ActusError(f"{raw['contractID']}: fixingDays {fixing!r} is not built (P<n>D)")
    ipcb = s("interestCalculationBase", "NT")
    if ipcb not in ("NT", "NTL", "NTIED"):
        raise ActusError(f"{raw['contractID']}: interestCalculationBase {ipcb!r} is not built")
    return Terms(
        kind=kind, cid=raw["contractID"], role=ROLES[raw["contractRole"]], currency=s("currency", "XXX"),
        sd=d("statusDate"), ied=d("initialExchangeDate"), md=d("maturityDate"),
        nt=n("notionalPrincipal"), ipnr=n("nominalInterestRate", Fraction(0)),
        dcc=s("dayCountConvention"), eomc=s("endOfMonthConvention", "SD"),
        bdc=s("businessDayConvention"), cal=s("calendar"),
        ipanx=d("cycleAnchorDateOfInterestPayment"), ipcl=s("cycleOfInterestPayment"),
        pdied=n("premiumDiscountAtIED", Fraction(0)), ipac=n("accruedInterest"),
        ipced=d("capitalizationEndDate"),
        rranx=d("cycleAnchorDateOfRateReset"), rrcl=s("cycleOfRateReset"),
        rrmlt=n("rateMultiplier", Fraction(1)), rrsp=n("rateSpread", Fraction(0)),
        rrmo=s("marketObjectCodeOfRateReset"), fixing=_dt.timedelta(days=int(fixing[1:-1])),
        rrnxt=n("nextResetRate"),
        prd=d("purchaseDate"), pprd=n("priceAtPurchaseDate", Fraction(0)),
        td=d("terminationDate"), ptd=n("priceAtTerminationDate", Fraction(0)),
        pranx=d("cycleAnchorDateOfPrincipalRedemption"), prcl=s("cycleOfPrincipalRedemption"),
        prnxt=n("nextPrincipalRedemptionPayment"), amd=d("amortizationDate"),
        ipcb=ipcb, ipcbanx=d("cycleAnchorDateOfInterestCalculationBase"),
        ipcbcl=s("cycleOfInterestCalculationBase"), ipcba=n("interestCalculationBaseAmount"),
        scmo=s("marketObjectCodeOfScalingIndex"), scied=n("scalingIndexAtContractDealDate"),
        scanx=d("cycleAnchorDateOfScalingIndex"), sccl=s("cycleOfScalingIndex"),
        scef=s("scalingEffect"),
    )


# ── schedule ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """One scheduled event: its type, the instant it is paid, the instant it is computed on."""

    kind: str
    at: _dt.datetime
    calc: _dt.datetime


def _derive_maturity(k: Terms) -> _dt.datetime:
    """LAM with no stated maturity: the last of ceil(NT / PRNXT) redemptions from the anchor."""
    if k.kind == "LAM" and k.prnxt and k.prcl and k.pranx:
        n = math.ceil(k.nt / k.prnxt)
        c = cal.Cycle.parse(k.prcl)
        eom = (k.eomc == "EOM" and c.months is not None
               and k.pranx.day == cal._last_day(k.pranx.year, k.pranx.month))
        return c.step(k.pranx, n - 1, eom)
    if k.kind in ("NAM", "ANN") and k.prnxt and k.prcl and k.pranx and k.amd is None:
        # walk the instalments: each pays the period's interest first, the rest redeems;
        # maturity is the instalment that clears the notional (ann11: 700 a month on 5,000 -> 8 months)
        c = cal.Cycle.parse(k.prcl)
        eom = (k.eomc == "EOM" and c.months is not None
               and k.pranx.day == cal._last_day(k.pranx.year, k.pranx.month))
        left, prev = k.nt, max(k.ied, k.sd)
        for i in range(10000):
            t = c.step(k.pranx, i, eom)
            redeem = k.prnxt - left * k.ipnr * cal.year_fraction(k.dcc, prev, t)
            if redeem <= 0:
                raise ActusError(f"{k.cid}: an instalment of {k.prnxt} never redeems the notional; no maturity")
            left -= redeem
            if left <= 0:
                return t
            prev = t
    if k.amd is not None:
        return k.amd
    raise ActusError(f"{k.cid}: no maturityDate and no rule here to derive one for {k.kind}")


def schedule(k: Terms) -> List[Event]:
    """Every event of the contract's life from its status date, in time and phase order."""
    md = k.md or _derive_maturity(k)
    raw: List[Tuple[str, _dt.datetime]] = [("IED", k.ied), ("MD", md)]

    def walk(anchor, cycle, end, include_end=True):
        return cal.schedule(anchor, cycle, end, k.eomc, include_end)

    if k.ipcl or k.ipanx:
        dates = walk(k.ipanx or k.ied, k.ipcl, md)
        if k.ipced is not None and k.ipced not in dates:
            dates = sorted(set(dates) | {k.ipced})
        for t in dates:
            if t >= k.ied:
                raw.append(("IPCI" if k.ipced is not None and t <= k.ipced else "IP", t))
    if k.kind != "PAM" and k.prcl:
        for t in walk(k.pranx or k.ied, k.prcl, md, include_end=False):
            if k.ied <= t < md:
                raw.append(("PR", t))
    if k.rrcl and k.rranx:
        first = True
        for t in walk(k.rranx, k.rrcl, md, include_end=False):
            if t >= md:
                continue
            raw.append(("RRF" if first and k.rrnxt is not None else "RR", t))
            if t >= k.sd:
                first = False
            if k.kind == "ANN" and k.prnxt is None:
                raw.append(("PRF", t))
    if k.kind == "ANN" and k.prnxt is None and k.pranx and k.pranx > k.ied:
        raw.append(("PRF", k.pranx - _dt.timedelta(days=1)))
    if k.ipcb == "NTL" and k.ipcbcl:
        for t in walk(k.ipcbanx or k.ied, k.ipcbcl, md, include_end=False):
            if k.ied < t < md:
                raw.append(("IPCB", t))
    if k.sccl and k.scanx:
        for t in walk(k.scanx, k.sccl, md, include_end=False):
            if k.ied < t < md:
                raw.append(("SC", t))
    if k.prd is not None:
        raw.append(("PRD", k.prd))
    if k.td is not None:
        raw.append(("TD", k.td))
    out = []
    for kind, t in raw:
        at, calc = cal.shift(t, k.bdc, k.cal)
        out.append(Event(kind, at, calc))
    out = [e for e in out if e.at >= k.sd]
    if k.td is not None:
        out = [e for e in out if e.at <= k.td]
    out.sort(key=lambda e: (e.at, PHASES.index(e.kind)))
    seen = set()
    for e in out:
        if (e.at, e.kind) in seen:
            raise ActusError(f"{k.cid}: two {e.kind} events on {e.at.isoformat()}")
        seen.add((e.at, e.kind))
    return out


# ── the contract type ───────────────────────────────────────────────────────

MONEY = ("notional", "accrued", "interest", "result", "opening")
MEMO = ("rate", "prnxt", "ipcb", "nsc", "isc")
_NATURE = {
    1: {"notional": Nature.ASSET, "accrued": Nature.ASSET, "interest": Nature.INCOME,
        "result": Nature.INCOME, "opening": Nature.EQUITY},
    -1: {"notional": Nature.LIABILITY, "accrued": Nature.LIABILITY, "interest": Nature.EXPENSE,
         "result": Nature.EXPENSE, "opening": Nature.ASSET},
}

#: Money flows in the lender's orientation: (name, phase, debit, credit). A
#: borrower's flow swaps debit and credit. Each has a mirror ``name~``.
_MONEY_FLOWS = [
    ("init_notional", "init", "notional", "opening"),
    ("init_accrued", "init", "accrued", "opening"),
    ("PRD_notional", "PRD", "notional", "cash"),
    ("PRD_accrued", "PRD", "accrued", "cash"),
    ("PRD_price", "PRD", "result", "cash"),
    ("IED_notional", "IED", "notional", "cash"),
    ("IED_premium", "IED", "result", "cash"),
    ("IED_accrued", "IED", "accrued", "interest"),
    ("TD_notional", "TD", "cash", "notional"),
    ("TD_accrued", "TD", "cash", "accrued"),
    ("TD_price", "TD", "cash", "result"),
    ("MD_notional", "MD", "cash", "notional"),
    ("MD_notional_scale", "MD", "cash", "result"),
    ("MD_accrued", "MD", "cash", "accrued"),
    ("MD_accrued_scale", "MD", "cash", "result"),
    ("PR_notional", "PR", "cash", "notional"),
    ("PR_scale", "PR", "cash", "result"),
    ("IP_pay", "IP", "cash", "accrued"),
    ("IP_scale", "IP", "cash", "result"),
    ("IPCI_capitalise", "IPCI", "notional", "accrued"),
] + [(f"{e}_accrue", e, "accrued", "interest")
     for e in ("PRD", "TD", "PR", "IP", "IPCI", "RR", "RRF", "PRF", "SC", "IPCB")]


def _nature(role: int, account: str) -> Nature:
    return Nature.ASSET if account == "cash" else _NATURE[role][account]


def _leg(role: int, debit: str, credit: str, memo: bool = False) -> Leg:
    if role == -1 and not memo:
        debit, credit = credit, debit
    natures = {}
    for a in (debit, credit):
        if memo:
            natures[a] = Nature.STOCK if not a.endswith("_set") else Nature.BOUNDARY
        else:
            natures[a] = _nature(role, a)
    eff_d = +1 if natures[debit] in (Nature.ASSET, Nature.EXPENSE, Nature.STOCK) else -1
    eff_c = -1 if natures[credit] in (Nature.ASSET, Nature.EXPENSE, Nature.STOCK) else +1
    acct = (lambda a: a if a == "cash" else "{id}." + a)
    return Leg("holder", acct(debit), acct(credit), (eff_d, eff_c))


def _flows(role: int) -> Tuple[FlowSpec, ...]:
    out = []
    for name, phase, debit, credit in _MONEY_FLOWS:
        out.append(FlowSpec(name, phase, (_leg(role, debit, credit),)))
        out.append(FlowSpec(name + "~", phase, (_leg(role, credit, debit),)))
    for phase in PHASES:
        for m in MEMO:
            out.append(FlowSpec(f"{phase}_{m}", phase, (_leg(role, m, m + "_set", memo=True),)))
            out.append(FlowSpec(f"{phase}_{m}~", phase, (_leg(role, m + "_set", m, memo=True),)))
    return tuple(out)


def _accounts(role: int):
    money = [("cash", Nature.ASSET)] + [("{id}." + a, _NATURE[role][a]) for a in MONEY]
    memo = []
    for m in MEMO:
        memo += [("{id}." + m, Nature.STOCK, FixedUnit(m)), ("{id}." + m + "_set", Nature.BOUNDARY, FixedUnit(m))]
    return {"holder": tuple(money + memo)}


def _signed(out: Dict[str, Fraction], name: str, amount: Fraction) -> None:
    """Record a flow amount, using the mirrored flow for a negative one."""
    if amount > 0:
        out[name] = out.get(name, Fraction(0)) + amount
    elif amount < 0:
        out[name + "~"] = out.get(name + "~", Fraction(0)) - amount


# ── pricing one period ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Start:
    """The state a book starts from: natural balances and the accrual clock.

    ``mode`` is ``fresh`` (nothing exists yet), ``opening`` (the contract
    exists at the status date) or ``purchase`` (bought at the first event,
    from a seller's state).
    """

    mode: str
    sd: _dt.datetime
    notional: Fraction = Fraction(0)
    accrued: Fraction = Fraction(0)
    rate: Fraction = Fraction(0)
    prnxt: Fraction = Fraction(0)
    ipcb: Fraction = Fraction(0)
    nsc: Fraction = Fraction(1)
    isc: Fraction = Fraction(1)


@dataclass
class Plan:
    """What the amounts function needs: the terms, the market, the events by period, the start."""

    k: Terms
    market: Optional[Market]
    events: List[Event]
    start: Start
    instants: List[_dt.datetime] = field(default_factory=list)
    by_period: Dict[int, List[Tuple[Event, _dt.datetime]]] = field(default_factory=dict)
    pr_dates: List[_dt.datetime] = field(default_factory=list)

    def __post_init__(self):
        self.instants = sorted({e.at for e in self.events})
        sd = self.start.sd
        for e in self.events:
            self.by_period.setdefault(self.instants.index(e.at), []).append((e, sd))
            sd = e.calc
        k = self.k
        if k.kind in ("LAM", "NAM", "ANN") and k.prcl:
            md = k.md or _derive_maturity(k)
            end = k.amd if (k.kind == "ANN" and k.amd is not None) else md
            self.pr_dates = [t for t in cal.schedule(k.pranx or k.ied, k.prcl, end, k.eomc) if t >= k.ied]


def _annuity(k: Terms, start: _dt.datetime, dates: List[_dt.datetime], notional: Fraction,
             accrued: Fraction, rate: Fraction) -> Fraction:
    """ACTUS A(s, T, n, a, r) = (n + a) / sum_i prod_{j<=i} (1 + r Y_j)^-1 over ``dates``, the redemptions still due."""
    rest = list(dates)
    if not rest:
        return Fraction(0)
    total, prod, prev = Fraction(0), Fraction(1), start
    for t in rest:
        prod *= 1 + rate * cal.year_fraction(k.dcc, prev, t)
        total += 1 / prod
        prev = t
    return (notional + accrued) / total


def _annuity_at(plan: Plan, ev: Event, notional: Fraction, accrued: Fraction, rate: Fraction) -> Fraction:
    """The instalment an annuity fixes at ``ev``.

    Before the first redemption the annuity is solved from the start of the
    first redemption period (the anchor less one cycle, not before the
    initial exchange) on the notional alone; ann07's published instalment
    (434.8666, fixed the day before its 2013-02-01 anchor) is that and not
    the same sum solved from the fixing day with its 30 days of interest
    (434.8672). Once redemptions have started it is solved from the event,
    on the notional plus the interest accrued so far.
    """
    k = plan.k
    if not any(t < ev.calc for t in plan.pr_dates):
        c = cal.Cycle.parse(k.prcl)
        anchor = k.pranx or k.ied
        eom = (k.eomc == "EOM" and c.months is not None
               and anchor.day == cal._last_day(anchor.year, anchor.month))
        start = max(k.ied, c.step(anchor, -1, eom))
        # a redemption falling on the start itself is still due (ann09 redeems on its exchange date)
        return _annuity(k, start, [t for t in plan.pr_dates if t >= start], notional, Fraction(0), rate)
    return _annuity(k, ev.calc, [t for t in plan.pr_dates if t > ev.calc], notional, accrued, rate)


def _amounts(terms: Mapping[str, Any], t: int, bal) -> Dict[str, Fraction]:
    plan: Plan = terms["plan"]
    k, out = plan.k, {}

    def b(name):
        return bal("holder", "{id}." + name)

    def set_memo(phase, name, new):
        _signed(out, f"{phase}_{name}", new - b(name))

    if t == 0:
        s = plan.start
        if s.mode == "opening":
            _signed(out, "init_notional", s.notional)
            _signed(out, "init_accrued", s.accrued)
        for m in MEMO:
            _signed(out, f"init_{m}", getattr(s, m))
    for ev, sd in plan.by_period.get(t, []):
        out.update(_price(plan, ev, sd, b, set_memo, bal))
    return out


def _price(plan: Plan, ev: Event, sd: _dt.datetime, b, set_memo, bal) -> Dict[str, Fraction]:
    k, out, E = plan.k, {}, ev.kind
    N, A, r = b("notional"), b("accrued"), b("rate")
    base = b("ipcb") if k.ipcb == "NTL" and k.kind != "PAM" else N
    acc = cal.year_fraction(k.dcc, sd, ev.calc) * r * base

    def accrue():
        _signed(out, f"{E}_accrue", acc)

    if E == "IED":
        _signed(out, "IED_notional", k.nt)
        _signed(out, "IED_premium", k.pdied)
        if k.ipac is not None:
            _signed(out, "IED_accrued", k.ipac)
        elif k.ipanx is not None and k.ipanx < ev.calc:
            _signed(out, "IED_accrued", cal.year_fraction(k.dcc, k.ipanx, ev.calc) * k.ipnr * k.nt)
        set_memo(E, "rate", k.ipnr)
        if k.kind != "PAM":
            set_memo(E, "ipcb", k.ipcba if k.ipcba is not None else k.nt)
            set_memo(E, "prnxt", _initial_prnxt(plan, ev))
    elif E == "IP":
        accrue()
        _signed(out, "IP_pay", A + acc)
        _signed(out, "IP_scale", (b("isc") - 1) * (A + acc))
    elif E == "IPCI":
        accrue()
        _signed(out, "IPCI_capitalise", A + acc)
    elif E == "PR":
        accrue()
        P = b("prnxt")
        if k.kind == "LAM":
            paid = min(P, N)
        else:
            paid = P - (A + acc)
            if abs(paid) > N:
                paid = N
        _signed(out, "PR_notional", paid)
        _signed(out, "PR_scale", (b("nsc") - 1) * paid)
    elif E == "MD":
        _signed(out, "MD_notional", N)
        _signed(out, "MD_notional_scale", (b("nsc") - 1) * N)
        _signed(out, "MD_accrued", A)
        _signed(out, "MD_accrued_scale", (b("isc") - 1) * A)
    elif E in ("RR", "RRF"):
        accrue()
        if E == "RRF":
            new = k.rrnxt
        else:
            # the corpus prices a reset on the observation at the reset date itself, also when
            # fixingDays is two days (ann16 has no observation before its 2013-07-01 reset)
            obs = plan.market.series(k.rrmo).at(ev.calc)
            new = k.rrmlt * obs + k.rrsp
        set_memo(E, "rate", new)
        if k.kind == "ANN":
            set_memo(E, "prnxt", _annuity_at(plan, ev, N, A + acc, new))
    elif E == "PRF":
        accrue()
        set_memo(E, "prnxt", _annuity_at(plan, ev, N, A + acc, r))
    elif E == "IPCB":
        accrue()
        set_memo(E, "ipcb", N)
    elif E == "SC":
        accrue()
        idx = plan.market.series(k.scmo).at(ev.calc) / k.scied
        if k.scef and k.scef[0] == "I":
            set_memo(E, "isc", idx)
        if k.scef and len(k.scef) > 1 and k.scef[1] == "N":
            set_memo(E, "nsc", idx)
    elif E == "PRD":
        s = plan.start
        s_acc = cal.year_fraction(k.dcc, sd, ev.calc) * s.rate * (s.ipcb if k.ipcb == "NTL" and k.kind != "PAM" else s.notional)
        _signed(out, "PRD_notional", s.notional)
        _signed(out, "PRD_accrued", s.accrued + s_acc)
        _signed(out, "PRD_price", k.pprd - s.notional - _borrower_gap(k, s.accrued + s_acc))
    elif E == "TD":
        accrue()
        _signed(out, "TD_notional", N)
        _signed(out, "TD_accrued", A + acc)
        _signed(out, "TD_price", k.ptd - N - _borrower_gap(k, A + acc))
    return out


def _borrower_gap(k: Terms, accrued: Fraction) -> Fraction:
    """The reference's sign for a borrower's accrued interest at a purchase or termination.

    The corpus prices both events as ``R x (price + Ipac + Y Ipnr Nt)`` with
    the state already signed by the role, so for a borrower (R = -1) the
    accrued interest it owes enters *against* the price instead of on top
    of it: nam04 terminates at 1,800 with 4.42 accrued and pays 1,795.58,
    and lam21 buys at 5,200 with 5.48 accrued and receives 5,194.52. A
    ledger that settles the accrued interest at face would pay 1,804.42 and
    receive 5,205.48. We follow the referee, so the cash is theirs, and the
    difference (twice the accrued interest) lands in ``result`` where it is
    visible. A lender is unaffected.
    """
    return 2 * accrued if k.role == -1 else Fraction(0)


def _initial_prnxt(plan: Plan, ev: Event) -> Fraction:
    k = plan.k
    if k.prnxt is not None:
        return k.prnxt
    if k.kind == "LAM":
        n = len([t for t in plan.pr_dates])
        return k.nt / n if n else Fraction(0)
    if k.kind == "ANN":
        return _annuity_at(plan, ev, k.nt, Fraction(0), k.ipnr)
    raise ActusError(f"{k.cid}: {k.kind} needs nextPrincipalRedemptionPayment")


def contract_type(role: int) -> ContractType:
    """The ACTUS contract type for a lender (1) or a borrower (-1)."""
    return ContractType(name=f"actus_{'RPA' if role == 1 else 'RPL'}", terms=("plan",), parties=("holder",),
                        accounts=_accounts(role), flows=_flows(role), amounts=_amounts)


_TYPES = {r: contract_type(r) for r in (1, -1)}


# ── running a contract ──────────────────────────────────────────────────────

@dataclass
class Run:
    """One contract run in one book: the group, the plan, and what the corpus reports per event."""

    group: Group
    plan: Plan
    cid: str
    horizon: Optional[_dt.datetime] = None

    def state(self, t: int, phase: str) -> Dict[str, Fraction]:
        """Natural balances after ``phase`` of period ``t``."""
        book = self.group.book("holder")
        idx = PHASES.index(phase)
        out = {}
        for a in ("cash",) + tuple(f"{self.cid}.{x}" for x in MONEY + MEMO):
            v = book.opening(a, t)
            for p in book.postings:
                if p.period == t and PHASES.index(p.phase) <= idx:
                    sign = 1 if _nature_of(book, a).debit_natured else -1
                    if p.debit == a:
                        v += sign * p.amount
                    elif p.credit == a:
                        v -= sign * p.amount
            out[a.split(".", 1)[-1]] = v
        return out

    def stream(self) -> List[Dict[str, Any]]:
        """Per reported event: date, type, payoff, and the signed state after it (ACTUS sign)."""
        R, rows = self.plan.k.role, []
        for e in self.plan.events:
            if self.horizon is not None and e.at > self.horizon:
                break
            t = self.plan.instants.index(e.at)
            before = self.state(t, PHASES[PHASES.index(e.kind) - 1])
            after = self.state(t, e.kind)
            rows.append({"date": e.at, "type": e.kind, "payoff": after["cash"] - before["cash"],
                         "notional": R * after["notional"], "rate": after["rate"],
                         "accrued": R * after["accrued"]})
        return rows


def _nature_of(book, name):
    return book._account(name).nature


def _simulate(k: Terms, market: Optional[Market], events: List[Event], start: Start) -> Run:
    plan = Plan(k, market, events, start)
    g = Group(periods=len(plan.instants), phases=PHASES)
    g.add_contract(Contract(k.cid, _TYPES[k.role], {"holder": "holder"},
                            {"plan": Value(plan, State.STATED, f"actus:{k.cid}")}, k.currency))
    g.run()
    return Run(g, plan, k.cid)


def _market(data: Mapping[str, Any]) -> Market:
    out = {}
    for code, body in (data or {}).items():
        pts = [(cal.instant(p["timestamp"]), _num(p["value"])) for p in body["data"]]
        out[code] = Series.of(code, pts)
    return Market(out)


def run(contract: Mapping[str, Any]) -> Run:
    """Run one ACTUS test-corpus entry (``terms`` and ``dataObserved``) on the engine."""
    k = parse_terms(contract["terms"])
    out = _run(k, _market(contract.get("dataObserved")))
    if contract.get("to"):
        out.horizon = cal.instant(contract["to"])        # the corpus reports to a horizon (ann08: 2019-02-01)
    return out


def _run(k: Terms, market: Market) -> Run:
    events = schedule(k)
    if k.ied < k.sd:
        start = Start("opening", k.sd, notional=k.nt, accrued=_opening_accrued(k), rate=k.ipnr,
                      prnxt=k.prnxt or Fraction(0), ipcb=k.ipcba if k.ipcba is not None else k.nt)
    else:
        start = Start("fresh", k.sd)
    if k.prd is None:
        return _simulate(k, market, events, start)
    # events on the purchase date itself are the seller's (lam02 buys after that day's redemption)
    before = [e for e in events if e.at <= k.prd and e.kind != "PRD"]
    after = [e for e in events if e.kind == "PRD" or e.at > k.prd]
    seller = _simulate(k, market, before, start) if before else None
    if seller is not None:
        last = len(seller.plan.instants) - 1
        s = seller.state(last, PHASES[-1])
        start = Start("purchase", before[-1].calc, **{x: s[x] for x in ("notional", "accrued", "rate", "prnxt",
                                                                        "ipcb", "nsc", "isc")})
    else:
        start = replace(start, mode="purchase")
    return _simulate(k, market, after, start)


def _opening_accrued(k: Terms) -> Fraction:
    """Interest accrued at the status date of a contract that already exists."""
    if k.ipac is not None:
        return k.ipac
    if k.ipanx is None or k.ipanx >= k.sd:
        return Fraction(0)
    last = k.ipanx
    if k.ipcl:
        c = cal.Cycle.parse(k.ipcl)
        i = 0
        while c.step(k.ipanx, i + 1, False) <= k.sd:
            i += 1
        last = c.step(k.ipanx, i, False)
    return cal.year_fraction(k.dcc, last, k.sd) * k.ipnr * k.nt
