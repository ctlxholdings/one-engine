"""ACTUS contracts on the ledger kernel: loans (PAM, LAM, LAX, NAM, ANN), call money (CLM),
undefined maturity (UMP), the cash position (CSH), and the plain vanilla swap (SWPPV), run as
two PAM legs (:func:`run_swap`).

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
* an analysis date (``AD``) reports the state at an instant and moves no
  money: interest accrues up to it and stays accrued. An instant stamped
  23:59:59 counts as the next midnight in a day count, so ad0's
  2013-03-31T23:59:59 reports the whole March interest that its 2013-04-01
  coupon then pays (ad0, one case: a mid-period AD would test more);
* a cash position (``CSH``) is a held balance with no schedule: it exists at
  its status date and reports only at its analysis dates (csh01..04).

Call money (CLM) and undefined maturity (UMP)
---------------------------------------------
Both capitalise interest on their cycle (``IPCI``) and have no redemption
schedule. The corpus fixed these readings:

* the capitalisation cycle starts at the initial exchange when no anchor is
  stated, so an exchange after the status date is also a (zero)
  capitalisation (clm12). The standard says IED + one cycle;
* the interest schedule of a CLM is its maturity alone, so a contract that
  already exists at its status date opens with no accrued interest unless
  it states some (clm03, clm08);
* an event on the status date is past: a contract exchanged on its status
  date opens with its notional and reports no IED (clm01, clm02);
* a rate reset with no anchor starts one cycle after the exchange, and an
  anchor with no cycle is a single reset (clm05, clm15);
* a CLM matures at its stated maturity, or, once called (an observed
  ``XD`` event), at the call date plus the notice period (``xDayNotice``),
  where it pays its interest (``IP``) and settles its notional (``STD``).
  Interest stops at the call: no capitalisation or reset falls on or after
  it, and nothing accrues between call and settlement (clm07, clm08: the
  settlement interest is exactly the interest to the call date). With a
  notice period the interest to the call is fixed at the call as an amount
  due (``called``), so the call reports no accrued interest (clm07, clm08);
  with none it is paid at once and the call reports it accrued (clm09,
  clm13, clm14). The settlement is reported even past the horizon
  (clm07); the horizon cuts only scheduled events;
* a UMP has no maturity: its schedule runs to the reporting horizon,
  which is excluded (ump04: a yearly cycle with the horizon on a cycle date
  ends one year before it).

The exotic linear amortizer (LAX)
---------------------------------
A LAM whose redemption, interest and reset schedules are arrays of
segments: segment ``i`` starts at its anchor and runs on its cycle up to
the next anchor (excluded), the last one to maturity; with no cycle, the
anchor alone. A redemption segment is ``DEC`` (principal redemptions,
``PR``) or ``INC`` (principal increases, ``PI``: the lender pays out more);
a reset anchor is ``FIX`` (``RRF`` to the array rate) or ``VAR`` (``RR`` to
the observation times ``rateMultiplier`` plus the array rate as spread).
Each ``PR``/``PI`` sets the instalment to its segment's amount before it
pays, so the corpus's silent ``PRF`` events are not reported (lax01..18).
With no stated maturity the contract matures at the redemption that would
take the notional to zero (lax04, lax14, lax16).
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
import re
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
PHASES = ("init", "AD", "XD", "PRD", "IED", "PR", "PI", "IP", "IPCI", "RR", "RRF", "PRF", "SC", "IPCB", "TD", "MD", "STD")

TYPES = ("PAM", "LAM", "LAX", "NAM", "ANN", "CLM", "UMP", "CSH")
#: Types with no redemption schedule of their own: interest only, notional at the end.
_BULLET = ("PAM", "CLM", "UMP")
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
    "scalingEffect", "xDayNotice", "creatorID", "counterpartyID",
})
#: LAX's array terms; any other type that states one refuses.
ARRAY_TERMS = frozenset({
    "arrayCycleAnchorDateOfPrincipalRedemption", "arrayCycleOfPrincipalRedemption",
    "arrayNextPrincipalRedemptionPayment", "arrayIncreaseDecrease",
    "arrayCycleAnchorDateOfInterestPayment", "arrayCycleOfInterestPayment",
    "arrayCycleAnchorDateOfRateReset", "arrayRate", "arrayFixedVariable",
})
BUILT_TERMS = BUILT_TERMS | ARRAY_TERMS

#: ACTUS terms we know and do not compute; each refuses by name.
KNOWN_UNBUILT = frozenset({
    "feeRate", "feeBasis", "feeAccrued", "cycleOfFee", "cycleAnchorDateOfFee",
    "prepaymentEffect", "prepaymentPeriod", "optionExerciseEndDate", "cycleOfOptionality",
    "cycleAnchorDateOfOptionality", "penaltyType", "penaltyRate",
    "lifeCap", "lifeFloor", "periodCap", "periodFloor", "cyclePointOfRateReset",
    "cyclePointOfInterestPayment", "delinquencyPeriod", "gracePeriod", "creditLineAmount",
    "marketValueObserved", "settlementCurrency", "x_day_notice", "arrayCycleOfRateReset",
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
    xdn: Optional[str] = None
    lax: Optional["Arrays"] = None


@dataclass(frozen=True)
class Arrays:
    """LAX's array terms, each a tuple (a single stated value is a one-entry array)."""

    pr_anchors: Tuple[_dt.datetime, ...]
    pr_cycles: Tuple[str, ...]
    pr_amounts: Tuple[Fraction, ...]
    pr_incdec: Tuple[str, ...]
    ip_anchors: Tuple[_dt.datetime, ...]
    ip_cycles: Tuple[str, ...]
    rr_anchors: Tuple[_dt.datetime, ...]
    rr_rates: Tuple[Fraction, ...]
    rr_fixvar: Tuple[str, ...]


def _array(v) -> list:
    return [] if v in (None, "") else (list(v) if isinstance(v, (list, tuple)) else [v])


def _parse_arrays(raw: Mapping[str, Any]) -> Arrays:
    a = {k: _array(raw.get(k)) for k in ARRAY_TERMS}
    out = Arrays(
        pr_anchors=tuple(cal.instant(x) for x in a["arrayCycleAnchorDateOfPrincipalRedemption"]),
        pr_cycles=tuple(str(x) for x in a["arrayCycleOfPrincipalRedemption"]),
        pr_amounts=tuple(_num(x) for x in a["arrayNextPrincipalRedemptionPayment"]),
        pr_incdec=tuple(str(x) for x in a["arrayIncreaseDecrease"]),
        ip_anchors=tuple(cal.instant(x) for x in a["arrayCycleAnchorDateOfInterestPayment"]),
        ip_cycles=tuple(str(x) for x in a["arrayCycleOfInterestPayment"]),
        rr_anchors=tuple(cal.instant(x) for x in a["arrayCycleAnchorDateOfRateReset"]),
        rr_rates=tuple(_num(x) for x in a["arrayRate"]),
        rr_fixvar=tuple(str(x) for x in a["arrayFixedVariable"]),
    )
    cid = raw.get("contractID")
    if not (len(out.pr_anchors) == len(out.pr_amounts) == len(out.pr_incdec) > 0):
        raise ActusError(f"{cid}: LAX redemption arrays (anchors, amounts, INC/DEC) differ in length")
    if set(out.pr_incdec) - {"INC", "DEC"} or set(out.rr_fixvar) - {"FIX", "VAR"}:
        raise ActusError(f"{cid}: LAX array flags {sorted(set(out.pr_incdec) | set(out.rr_fixvar))} are not built")
    if not (len(out.rr_anchors) == len(out.rr_rates) == len(out.rr_fixvar)):
        raise ActusError(f"{cid}: LAX rate-reset arrays differ in length")
    return out


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
    arrays = sorted(set(raw) & ARRAY_TERMS)
    if arrays and kind != "LAX":
        raise ActusError(f"{raw.get('contractID')}: array term(s) {arrays} are built for LAX only")
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
        scef=s("scalingEffect"), xdn=s("xDayNotice"),
        lax=_parse_arrays(raw) if kind == "LAX" else None,
    )


# ── schedule ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Event:
    """One scheduled event: its type, the instant it is paid, the instant it is computed on."""

    kind: str
    at: _dt.datetime
    calc: _dt.datetime
    #: "schedule", or "observed" for an event an observed event caused (a call's settlement):
    #: the reporting horizon cuts only scheduled events
    source: str = "schedule"
    #: the date before any business-day shift: market observations are read on it (clm09, clm10)
    sched: Optional[_dt.datetime] = None


def _notice(k: Terms, t: _dt.datetime) -> _dt.datetime:
    """``t`` plus the CLM notice period ``xDayNotice`` (P<n>D|W|M|Y; P0D is the same instant)."""
    m = re.match(r"^P(\d+)([DWMY])$", k.xdn or "")
    if not m:
        raise ActusError(f"{k.cid}: xDayNotice {k.xdn!r} is not built (P<n><D|W|M|Y>)")
    n, unit = int(m.group(1)), m.group(2)
    return t if n == 0 else cal.Cycle.parse(f"P{n}{unit}L0").step(t, 1, False)


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


def schedule(k: Terms, analysis: Tuple[_dt.datetime, ...] = (), called: Optional[_dt.datetime] = None,
             horizon: Optional[_dt.datetime] = None) -> List[Event]:
    """Every event of the contract's life from its status date, in time and phase order.

    ``analysis`` are the instants at which the state is to be reported
    (``AD`` events), ``called`` the instant a CLM was called (an observed
    ``XD``) and ``horizon`` the reporting horizon, which is where a UMP's
    schedule ends. None of these is a contract term, so they come with the run.
    """
    if k.kind == "CSH":
        return [Event("AD", t, t) for t in sorted(analysis) if t >= k.sd]
    if k.kind in ("CLM", "UMP"):
        return _schedule_open(k, analysis, called, horizon)
    if k.kind == "LAX":
        return _schedule_lax(k, analysis)
    md = k.md or _derive_maturity(k)
    raw: List[Tuple[str, _dt.datetime]] = [("IED", k.ied), ("MD", md)]
    raw += [("AD", t) for t in analysis]

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
    elif k.rranx and k.rranx < md:
        # an anchor with no cycle is a single reset, as for CLM (clm15); swppv01's floating leg resets
        # on its exchange date. No loan in the corpus states an anchor without a cycle
        raw.append(("RRF" if k.rrnxt is not None else "RR", k.rranx))
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
    # a maturity derived from the redemption cycle is a cycle date, and shifts (ann28: Sunday
    # 2013-09-01 under SCF is paid Monday 09-02); a stated one does not (clm10)
    return _order(k, raw, unshifted=UNSHIFTED if k.md is not None else UNSHIFTED - {"MD"})


#: Events dated by a term rather than a cycle. The standard shifts only cycle dates by the
#: business-day convention; clm10 matures on Sunday 2015-09-20 under SCP and reports it there,
#: but computes on the shifted day all the same: its interest runs to Friday 09-18.
UNSHIFTED = frozenset({"IED", "MD", "STD", "TD", "PRD", "AD", "XD"})


def _order(k: Terms, raw, after_sd: bool = False, unshifted=UNSHIFTED) -> List[Event]:
    """Shift each cycle (kind, date) by the business-day rule, drop what lies before the status
    date (or on it, with ``after_sd``) or after a termination, and sort in time and phase order."""
    out = []
    for kind, t, *src in raw:
        at, calc = cal.shift(t, k.bdc, k.cal)
        if kind in unshifted:
            at = t
        out.append(Event(kind, at, calc, *(src or ["schedule"]), sched=t))
    out = [e for e in out if (e.at > k.sd if after_sd else e.at >= k.sd)]
    if k.td is not None:
        out = [e for e in out if e.at <= k.td]
    out.sort(key=lambda e: (e.at, PHASES.index(e.kind)))
    seen = set()
    for e in out:
        if (e.at, e.kind) in seen:
            raise ActusError(f"{k.cid}: two {e.kind} events on {e.at.isoformat()}")
        seen.add((e.at, e.kind))
    return out


def _schedule_open(k: Terms, analysis, called, horizon) -> List[Event]:
    """CLM and UMP: interest capitalised on its cycle, no redemption schedule (rules in the module notes)."""
    if k.kind == "CLM":
        md = _notice(k, called) if called is not None else k.md
        if md is None:
            raise ActusError(f"{k.cid}: a CLM with no maturity that is never called is not built")
        stop = called if called is not None else md            # interest events stop at the call
    else:
        if horizon is None:
            raise ActusError(f"{k.cid}: a UMP has no maturity; it needs a reporting horizon (to)")
        md = stop = horizon
    src = "observed" if called is not None else "schedule"
    raw = [("IED", k.ied)] + [("AD", t) for t in analysis]
    if k.kind == "CLM":
        raw += [("IP", md, src), ("STD" if called is not None else "MD", md, src)]
    if called is not None:
        raw.append(("XD", called))
    if k.ipcl:
        for t in cal.schedule(k.ipanx or k.ied, k.ipcl, stop, k.eomc, False):
            if k.ied <= t < stop:
                raw.append(("IPCI", t))
    rranx = k.rranx or (k.ied + _cycle_delta(k.rrcl, k.ied) if k.rrcl else None)
    if rranx is not None:
        dates = cal.schedule(rranx, k.rrcl, stop, k.eomc, False) if k.rrcl else [rranx]
        raw += [("RR", t) for t in dates if t < stop]
    if k.td is not None:
        raw.append(("TD", k.td))
    # a CLM's one interest payment is dated by its maturity, not by a cycle
    return _order(k, raw, after_sd=True, unshifted=UNSHIFTED | ({"IP"} if k.kind == "CLM" else set()))


def _segments(anchors, cycles, end, include_end):
    """An ACTUS array schedule: per anchor, its cycle's dates up to the next anchor (excluded),
    the last up to ``end``; with no cycles, the anchors alone. Yields (segment index, date)."""
    for i, a in enumerate(anchors):
        stop = anchors[i + 1] if i + 1 < len(anchors) else end
        if not cycles:
            if a < end or (include_end and a == end):
                yield i, a
            continue
        c = cycles[i] if len(cycles) == len(anchors) else cycles[0]
        last = i + 1 == len(anchors)
        for t in cal.schedule(a, c, stop, "SD", include_end and last):
            if t < stop or (last and include_end and t == stop):
                yield i, t


def _lax_steps(k: Terms, md: Optional[_dt.datetime]):
    """LAX principal events in order: (kind, date, amount). With no maturity, walks the segments
    until a redemption would take the notional to zero; that date is the maturity."""
    a, n, out = k.lax, k.nt, []
    end = md or _dt.datetime(9999, 1, 1)
    for i, t in _segments(a.pr_anchors, a.pr_cycles, end, False):
        if md is None and a.pr_incdec[i] == "DEC" and n - a.pr_amounts[i] <= 0:
            return out, t
        n += a.pr_amounts[i] if a.pr_incdec[i] == "INC" else -a.pr_amounts[i]
        out.append(("PI" if a.pr_incdec[i] == "INC" else "PR", t, a.pr_amounts[i]))
        if len(out) > 10000:
            break
    if md is None:
        raise ActusError(f"{k.cid}: LAX with no maturity never redeems its notional")
    return out, md


def _schedule_lax(k: Terms, analysis) -> List[Event]:
    a = k.lax
    steps, md = _lax_steps(k, k.md)
    raw = [("IED", k.ied), ("MD", md)] + [("AD", t) for t in analysis]
    raw += [(kind, t) for kind, t, _ in steps if k.ied <= t < md]
    raw += [("IP", t) for _, t in _segments(a.ip_anchors, a.ip_cycles, md, True) if t >= k.ied]
    if a.ip_anchors and not a.ip_cycles and md not in a.ip_anchors:
        raw.append(("IP", md))                              # anchors alone: interest is still paid at maturity
    raw += [("RRF" if a.rr_fixvar[i] == "FIX" else "RR", t) for i, t in enumerate(a.rr_anchors) if t < md]
    if k.td is not None:
        raw.append(("TD", k.td))
    return _order(k, raw, unshifted=UNSHIFTED if k.md is not None else UNSHIFTED - {"MD"})


def _lax_at(values, anchors, t) -> Any:
    """The array value in force at ``t``: that of the last anchor at or before it."""
    i = max(j for j, a in enumerate(anchors) if a <= t)
    return values[i]


def _cycle_delta(cycle: str, at: _dt.datetime) -> _dt.timedelta:
    """One step of ``cycle`` from ``at``, as a duration."""
    return cal.Cycle.parse(cycle).step(at, 1, False) - at


# ── the contract type ───────────────────────────────────────────────────────

MONEY = ("notional", "accrued", "called", "interest", "result", "opening")
MEMO = ("rate", "prnxt", "ipcb", "nsc", "isc")
_NATURE = {
    1: {"notional": Nature.ASSET, "accrued": Nature.ASSET, "called": Nature.ASSET, "interest": Nature.INCOME,
        "result": Nature.INCOME, "opening": Nature.EQUITY},
    -1: {"notional": Nature.LIABILITY, "accrued": Nature.LIABILITY, "called": Nature.LIABILITY,
         "interest": Nature.EXPENSE,
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
    ("XD_call", "XD", "called", "accrued"),
    ("IP_called", "IP", "cash", "called"),
    ("STD_notional", "STD", "cash", "notional"),
    ("PI_notional", "PI", "notional", "cash"),
] + [(f"{e}_accrue", e, "accrued", "interest")
     for e in ("AD", "XD", "PI", "PRD", "TD", "PR", "IP", "IPCI", "RR", "RRF", "PRF", "SC", "IPCB")]


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
    #: when a CLM was called (an observed XD); nothing accrues from then on
    called: Optional[_dt.datetime] = None
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
    # a cash position bears no interest and states no day count; a called CLM stops accruing
    if k.kind == "CSH" or (plan.called is not None and sd >= plan.called):
        acc = Fraction(0)
    else:
        acc = cal.year_fraction(k.dcc, sd, ev.calc) * r * base

    def accrue():
        _signed(out, f"{E}_accrue", acc)

    if E == "AD":
        accrue()                          # reported, not paid: the next payment settles it
    elif E == "XD":
        accrue()
        if any(e.kind == "STD" and e.at > ev.at for e in plan.events):
            _signed(out, "XD_call", A + acc)  # a notice period: the interest to the call becomes due
    elif E == "STD":
        _signed(out, "STD_notional", N)
    elif E == "IED":
        _signed(out, "IED_notional", k.nt)
        _signed(out, "IED_premium", k.pdied)
        if k.ipac is not None:
            _signed(out, "IED_accrued", k.ipac)
        elif k.ipanx is not None and k.ipanx < ev.calc:
            _signed(out, "IED_accrued", cal.year_fraction(k.dcc, k.ipanx, ev.calc) * k.ipnr * k.nt)
        set_memo(E, "rate", k.ipnr)
        if k.kind not in _BULLET:
            set_memo(E, "ipcb", k.ipcba if k.ipcba is not None else k.nt)
            set_memo(E, "prnxt", _initial_prnxt(plan, ev))
    elif E == "IP":
        accrue()
        _signed(out, "IP_pay", A + acc)
        _signed(out, "IP_scale", (b("isc") - 1) * (A + acc))
        _signed(out, "IP_called", b("called"))
    elif E == "IPCI":
        accrue()
        _signed(out, "IPCI_capitalise", A + acc)
    elif E == "PI":
        accrue()
        P = _lax_at(k.lax.pr_amounts, k.lax.pr_anchors, ev.sched)
        set_memo(E, "prnxt", P)
        _signed(out, "PI_notional", P)
    elif E == "PR":
        accrue()
        if k.kind == "LAX":
            P = _lax_at(k.lax.pr_amounts, k.lax.pr_anchors, ev.sched)
            set_memo(E, "prnxt", P)
        else:
            P = b("prnxt")
        if k.kind in ("LAM", "LAX"):
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
        if k.kind == "LAX":
            spread = k.lax.rr_rates[k.lax.rr_anchors.index(ev.sched)]
            new = spread if E == "RRF" else k.rrmlt * plan.market.series(k.rrmo).at(ev.sched) + spread
        elif E == "RRF":
            new = k.rrnxt
        else:
            # the corpus prices a reset on the observation at the reset date itself, also when
            # fixingDays is two days (ann16 has no observation before its 2013-07-01 reset), and
            # on the scheduled date, not the business-day-shifted one: clm09 resets on Monday
            # 2015-08-17 (SCF) and clm10 on Friday 2015-08-14 (SCP) on the Sunday 08-16 print
            obs = plan.market.series(k.rrmo).at(ev.sched or ev.calc)
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
    if k.kind == "LAX":
        return k.lax.pr_amounts[0]       # each PR/PI resets it to its segment's amount
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

    def legs(self) -> Tuple["Run", ...]:
        """The runs whose books hold this contract: itself (a swap has two, :class:`SwapRun`)."""
        return (self,)

    def stream(self) -> List[Dict[str, Any]]:
        """Per reported event: date, type, payoff, and the signed state after it (ACTUS sign)."""
        R, rows = self.plan.k.role, []
        for e in self.plan.events:
            if self.horizon is not None and e.at > self.horizon and e.source == "schedule":
                continue
            t = self.plan.instants.index(e.at)
            before = self.state(t, PHASES[PHASES.index(e.kind) - 1])
            after = self.state(t, e.kind)
            rows.append({"date": e.at, "type": e.kind, "payoff": after["cash"] - before["cash"],
                         "notional": R * after["notional"], "rate": after["rate"],
                         "accrued": R * after["accrued"]})
        return rows


def _nature_of(book, name):
    return book._account(name).nature


def _simulate(k: Terms, market: Optional[Market], events: List[Event], start: Start,
              called: Optional[_dt.datetime] = None) -> Run:
    plan = Plan(k, market, events, start, called)
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


def run(contract: Mapping[str, Any], analysis: Tuple[str, ...] = ()) -> Run:
    """Run one ACTUS test-corpus entry (``terms``, ``dataObserved``, ``eventsObserved``) on the engine.

    Observed ``AD`` events and any ``analysis`` instants (ISO text) become
    analysis-date events. Any other observed event (a credit event, an
    exercise) is not built and refuses by name. A swap (SWPPV) runs as its
    two legs (:func:`run_swap`).
    """
    if contract["terms"].get("contractType") in COMPOSITE:
        if analysis or contract.get("eventsObserved"):
            raise ActusError(f"{contract['terms'].get('contractID')}: observed events are not built for a swap")
        return run_swap(contract)
    k = parse_terms(contract["terms"])
    observed = contract.get("eventsObserved") or []
    allowed = {"AD", "XD"} if k.kind == "CLM" else {"AD"}
    other = sorted({e.get("type") for e in observed} - allowed)
    if other:
        raise ActusError(f"{k.cid}: observed event type(s) {other} are not built for {k.kind}")
    ads = {cal.instant(e["time"]) for e in observed if e["type"] == "AD"} | {cal.instant(t) for t in analysis}
    calls = sorted(cal.instant(e["time"]) for e in observed if e["type"] == "XD")
    if len(calls) > 1:
        raise ActusError(f"{k.cid}: more than one call (XD) observed")
    horizon = cal.instant(contract["to"]) if contract.get("to") else None
    out = _run(k, _market(contract.get("dataObserved")), tuple(sorted(ads)), calls[0] if calls else None, horizon)
    out.horizon = horizon                                # the corpus reports to a horizon (ann08: 2019-02-01)
    return out


def _run(k: Terms, market: Market, analysis: Tuple[_dt.datetime, ...] = (),
         called: Optional[_dt.datetime] = None, horizon: Optional[_dt.datetime] = None) -> Run:
    events = schedule(k, analysis, called, horizon)
    if k.kind == "CSH":
        start = Start("opening", k.sd, notional=k.nt)
    elif k.ied < k.sd or (k.kind in ("CLM", "UMP") and k.ied <= k.sd):
        start = Start("opening", k.sd, notional=k.nt, accrued=_opening_accrued(k), rate=k.ipnr,
                      prnxt=k.prnxt or Fraction(0), ipcb=k.ipcba if k.ipcba is not None else k.nt)
    else:
        start = Start("fresh", k.sd)
    if k.prd is None:
        return _simulate(k, market, events, start, called)
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
    if k.kind in ("CLM", "UMP"):
        return Fraction(0)                # interest is due at maturity only (clm03, clm08)
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


# ── the plain vanilla swap (SWPPV): two loans, one each way ──────────────────

#: Contract types built from others: a swap runs as two PAM legs.
COMPOSITE = ("SWPPV",)
#: SWPPV roles: pay fixed (the fixed leg borrowed, the floating leg lent) or receive fixed
SWAP_ROLES = {"PF": -1, "RF": 1}
#: the swap's own terms, which no leg takes
_SWAP_ONLY = {"contractType", "contractRole", "nominalInterestRate2", "deliverySettlement",
              "priceAtPurchaseDate", "priceAtTerminationDate", "fixingPeriod"}
#: the floating leg's reset terms, which the fixed leg does not take
_RESET_TERMS = {"cycleAnchorDateOfRateReset", "cycleOfRateReset", "rateMultiplier", "rateSpread",
                "marketObjectCodeOfRateReset", "fixingDays", "nextResetRate"}


@dataclass
class SwapRun:
    """A plain vanilla swap run as its two legs, and the stream the corpus reports for it.

    Each leg is a PAM in a book of its own, with the swap's notional, dates
    and interest cycle: the fixed leg at ``nominalInterestRate``, the
    floating leg at ``nominalInterestRate2`` and reset on the swap's reset
    terms. A payer of fixed borrowed the fixed leg and lent the floating
    one; a receiver the reverse. The notionals exchanged at the start and at
    maturity are equal and opposite, so the swap pays nothing then.
    """

    fixed: Run
    floating: Run
    role: int
    net: bool

    def legs(self) -> Tuple[Run, Run]:
        return self.fixed, self.floating

    def stream(self) -> List[Dict[str, Any]]:
        """The swap's events with the corpus's reading (each rule pinned in tests/test_case_actus.py).

        * interest: ``IPFX`` (the fixed leg's payment) and ``IPFL`` (the
          floating leg's) when the legs are delivered, one ``IP`` with
          their sum when settled net (``deliverySettlement`` = ``S``);
        * the state after each event is the fixed leg's notional and
          accrued interest (signed by the swap's role) and the floating
          leg's rate (swppv12, swppv14: a purchase reports the fixed leg's
          accrued interest alone);
        * a reset is the floating leg's; a start, maturity, purchase or
          termination pays the sum of the two legs' payoffs.
        """
        fx, at_instant = {}, {}
        for r in self.fixed.stream():
            fx[(r["date"], r["type"])] = r
            at_instant.setdefault(r["date"], []).append(r)
        fl = self.floating.stream()
        rows = []
        for r in fl:
            at, kind = r["date"], r["type"]
            if kind == "AD":
                continue
            # a reset: the fixed leg as it stands at that phase of the instant (swppv14 resets, then
            # terminates, on 2015-07-01: the reset reports the notional still outstanding)
            f = fx.get((at, kind)) or [x for x in at_instant[at] if PHASES.index(x["type"]) <= PHASES.index(kind)][-1]
            state = {"notional": f["notional"], "rate": r["rate"], "accrued": f["accrued"]}
            if kind == "IP" and not self.net:
                rows.append({"date": at, "type": "IPFX", "payoff": f["payoff"], **state})
                rows.append({"date": at, "type": "IPFL", "payoff": r["payoff"], **state})
                continue
            payoff = r["payoff"] + (f["payoff"] if kind != "RR" else Fraction(0))
            rows.append({"date": at, "type": kind, "payoff": payoff, **state})
        return rows


def _leg_terms(raw: Mapping[str, Any], fixed: bool, role: int, prices: Tuple[Any, Any]) -> Dict[str, Any]:
    leg = {k: v for k, v in raw.items() if k not in _SWAP_ONLY and not (fixed and k in _RESET_TERMS)}
    leg.update(contractType="PAM", contractID=f"{raw['contractID']}_{'fixed' if fixed else 'floating'}",
               contractRole="RPA" if role == 1 else "RPL",
               nominalInterestRate=raw["nominalInterestRate"] if fixed else raw["nominalInterestRate2"])
    if not fixed and raw.get("fixingPeriod") not in (None, "P0D"):
        raise ActusError(f"{raw['contractID']}: a fixing period of {raw['fixingPeriod']} is not built")
    for term, price in zip(("priceAtPurchaseDate", "priceAtTerminationDate"), prices):
        if price is not None:
            leg[term] = str(price)
    return leg


def run_swap(contract: Mapping[str, Any]) -> SwapRun:
    """Run a SWPPV entry of the corpus as two PAM legs (see :class:`SwapRun`).

    A purchase or termination pays the swap's stated price and nothing for
    the interest accrued (swppv12..14: the buyer then receives each leg's
    whole next coupon). Each PAM leg prices its own event as price plus or
    minus its accrued interest, so the price is put on the fixed leg, the
    floating leg at par, with both legs' accrued interest taken back out:
    the legs are run once at par to read it. The difference from par lands
    in each leg's ``result``.

    Refused: a role other than PF / RF, a settlement other than D / S, a
    fixing period, and a reset on a date with no fixed-leg event (the corpus
    has none, so what accrued interest it reports is not known).
    """
    raw = contract["terms"]
    if raw.get("contractRole") not in SWAP_ROLES:
        raise ActusError(f"{raw.get('contractID')}: swap role {raw.get('contractRole')!r} is not built "
                         f"({sorted(SWAP_ROLES)})")
    if raw.get("deliverySettlement") not in ("D", "S"):
        raise ActusError(f"{raw.get('contractID')}: deliverySettlement {raw.get('deliverySettlement')!r} "
                         f"is not D or S")
    R, nt = SWAP_ROLES[raw["contractRole"]], _num(raw["notionalPrincipal"])
    data = contract.get("dataObserved")

    def legs(prices):
        par = tuple(nt if p is not None else None for p in prices)
        return (run({"terms": _leg_terms(raw, True, R, prices), "dataObserved": data}),
                run({"terms": _leg_terms(raw, False, -R, par), "dataObserved": data}))

    prd, td = raw.get("purchaseDate"), raw.get("terminationDate")
    f, l = legs((nt if prd else None, nt if td else None))
    if prd or td:
        # each leg's accrued interest at the event, read off its payoff at par: a leg of role r pays
        # -r P - A at a purchase and receives r P + A at a termination (A its natural accrued)
        acc = {"PRD": Fraction(0), "TD": Fraction(0)}
        for leg, r in ((f, R), (l, -R)):
            for x in leg.stream():
                if x["type"] == "PRD":
                    acc["PRD"] += -x["payoff"] - r * nt
                elif x["type"] == "TD":
                    acc["TD"] += x["payoff"] - r * nt
        f, l = legs((nt + _num(raw["priceAtPurchaseDate"]) - R * acc["PRD"] if prd else None,
                     nt + R * (_num(raw["priceAtTerminationDate"]) - acc["TD"]) if td else None))
    own = {r["date"] for r in f.stream()}
    between = sorted(r["date"] for r in l.stream() if r["type"] == "RR" and r["date"] not in own)
    if between:
        raise ActusError(f"{raw['contractID']}: a reset on {between[0].date()} falls between the fixed leg's "
                         f"events; what the swap reports there is not built")
    return SwapRun(f, l, R, raw["deliverySettlement"] == "S")
