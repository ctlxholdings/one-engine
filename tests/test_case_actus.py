"""The golds loan cases (plan step 7): the ACTUS corpus run on the engine.

Referee
-------
The ACTUS Financial Research Foundation publishes, for 109 loans, both the
contract terms and the expected dated event stream: per event its type,
payoff and the state after it (notional, nominal rate, accrued interest).
The files are vendored verbatim in ``cases/reference/actus/`` (CC BY-SA
4.0, ``actusfrf/actus-tests``); we authored no number on the answer side.

Every contract must reproduce its stream exactly: the same events on the
same dates in the same order, and every value within 1e-8 relative (the
corpus prints some rates to ten digits; the worst gap measured is 1e-10).
golds, the system this case comes from, reported 108 of 109 with lam18
refused; here all 109 agree.

The remaining tests pin each rule the corpus forced, so a wrong one names
itself, and check the ledger side the standard does not cover: the books
balance, every flow is paired, a borrower's positions are liabilities.
"""
import copy
import datetime as dt
import json
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.dates import instant as I
from one_engine.kernel import Nature
from one_engine.packs import actus

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases" / "reference" / "actus"
CORPUS = {}
for kind in ("pam", "lam", "nam", "ann"):
    CORPUS.update(json.loads((HERE / f"actus-tests-{kind}.json").read_text()))
FIELDS = (("payoff", "payoff"), ("notional", "notionalPrincipal"), ("rate", "nominalInterestRate"),
          ("accrued", "accruedInterest"))


def rows(cid):
    return actus.run(CORPUS[cid]).stream()


def first(cid, kind):
    return next(r for r in rows(cid) if r["type"] == kind)


def test_the_corpus_is_all_here():
    kinds = [c["terms"]["contractType"] for c in CORPUS.values()]
    assert len(CORPUS) == 109
    assert {k: kinds.count(k) for k in ("PAM", "LAM", "NAM", "ANN")} == {"PAM": 25, "LAM": 31, "NAM": 22, "ANN": 31}


@pytest.mark.parametrize("cid", sorted(CORPUS))
def test_every_contract_agrees_event_by_event(cid):
    case = CORPUS[cid]
    r = actus.run(case)
    got = r.stream()
    want = case["results"]
    assert [(x["date"], x["type"]) for x in got] == [(I(w["eventDate"]), w["eventType"]) for w in want]
    bad = [(g["date"].isoformat(), g["type"], ours)
           for g, w in zip(got, want) for ours, theirs in FIELDS
           if abs(float(g[ours]) - float(w[theirs])) > 1e-8 * max(1, abs(float(w[theirs])))]
    assert bad == []
    # the ledger side: every period closed on the per-unit identity, every flow paired
    book = r.group.book("holder")
    assert all(v == 0 for t in range(len(r.plan.instants)) for v in book.imbalance(t).values())
    assert r.group.unpaired_flows() == []


# ── the rules the corpus forced, one at a time ──────────────────────────────

def test_a_borrower_holds_its_loan_as_liabilities_and_pays_interest_expense():
    r = actus.run(CORPUS["pam03"])                                    # RPL
    book = r.group.book("holder")
    assert book._account("pam03.notional").nature is Nature.LIABILITY
    assert book._account("pam03.interest").nature is Nature.EXPENSE
    assert book._account("cash").nature is Nature.ASSET
    t = r.plan.instants.index(I("2013-06-01"))
    assert book.balance("pam03.notional", t) == 3000                  # natural: owed 3,000
    assert first("pam03", "IED")["notional"] == -3000                 # ACTUS sign: R x natural


def test_state_variables_sit_in_units_of_their_own():
    book = actus.run(CORPUS["lam01"]).group.book("holder")
    assert book._account("lam01.rate").unit == "rate" and book._account("lam01.prnxt").unit == "prnxt"
    assert book._account("lam01.notional").unit == "USD"


def test_a_borrowers_accrued_interest_enters_against_the_termination_price():
    """nam04 (a borrower) terminates at 1,800 with interest accrued since its last payment.
    The referee pays 1,800 less that interest; the gap to settling it at face
    (twice the accrued interest) is posted to the result account, not hidden."""
    r = actus.run(CORPUS["nam04"])
    book = r.group.book("holder")
    td = [x for x in r.stream() if x["type"] == "TD"][0]
    t = r.plan.instants.index(td["date"])
    here = [p for p in book.postings if p.period == t and p.phase == "TD"]
    acc = [p.amount for p in here if (p.debit, p.credit) == ("nam04.interest", "nam04.accrued")][0]
    assert td["payoff"] == -(1800 - acc)                                # -1,795.58, not -1,804.42
    assert abs(float(td["payoff"]) - -1795.5758424256537) < 1e-9
    result = [p.amount for p in here if "nam04.result" in (p.debit, p.credit)]
    assert result == [abs(1800 - book.opening("nam04.notional", t) - 2 * acc)]


def test_the_purchase_date_belongs_to_the_seller():
    prd = first("lam02", "PRD")
    assert prd["notional"] == 2500 and prd["accrued"] == 0 and prd["payoff"] == 5100


def test_an_annuity_redeeming_on_its_exchange_date_counts_that_redemption():
    pr = first("ann09", "PR")
    assert pr["date"] == I("2013-01-01") and abs(float(pr["payoff"]) - 400.071084163282) < 1e-9


def test_an_annuity_fixed_before_redemptions_start_is_solved_from_the_period_start():
    pr = first("ann07", "PR")                                          # PRF on 2013-01-31, anchor 02-01
    assert abs(float(pr["payoff"]) - 400.89399137862) < 1e-9          # not 400.8946 (solved from 01-31)


def test_a_maturity_is_derived_when_not_stated():
    assert rows("ann11")[-1]["date"] == I("2013-09-01")                # 700 a month on 5,000
    assert rows("ann28")[-1]["date"] == I("2013-09-02")                # the same, shifted off a Sunday
    assert rows("lam01")[-1]["date"] == I("2013-11-01")                # 5,000 / 500 = 10 redemptions
    # In the corpus the instalments are large enough that counting 5,000 / 700 gives the same
    # date as walking them; a dearer loan separates the two. An instalment pays the period's
    # interest first, so maturity is where what is left fits in one instalment.
    case = copy.deepcopy(CORPUS["ann11"])
    case["terms"].update({"nextPrincipalRedemptionPayment": "520", "nominalInterestRate": "0.12"})
    case["results"] = []
    out = actus.run(case).stream()
    md = out[-1]
    assert md["type"] == "MD" and 0 < md["payoff"] <= 520
    assert len([x for x in out if x["type"] == "PR"]) >= 10           # ceil(5,000 / 520) = 10 would be too few


def test_a_reset_reads_the_observation_on_the_reset_date():
    rr = first("ann16", "RR")
    assert rr["date"] == I("2013-07-01") and rr["rate"] == F("0.011654320987654319") + F("0.1")


def test_the_report_stops_at_its_horizon_and_the_contract_does_not():
    r = actus.run(CORPUS["ann08"])
    assert r.stream()[-1]["date"] == I("2019-02-01")
    assert r.plan.events[-1].at > I("2019-02-01")


def test_terms_outside_the_built_vocabulary_refuse_by_name():
    for extra, msg in (({"feeRate": "0.01"}, "does not compute"), ({"colour": "blue"}, "unknown term")):
        case = copy.deepcopy(CORPUS["pam01"])
        case["terms"].update(extra)
        with pytest.raises(actus.ActusError, match=msg):
            actus.run(case)
    for key, value in (("contractType", "SWAPS"), ("contractRole", "BUY")):
        case = copy.deepcopy(CORPUS["pam01"])
        case["terms"][key] = value
        with pytest.raises(actus.ActusError, match="not built"):
            actus.run(case)


# ── the plain vanilla swap (SWPPV): two PAM legs, one each way ──────────────

SWAPS = json.loads((HERE / "actus-tests-swppv.json").read_text())


def swap_row(cid, date, kind):
    return next(r for r in actus.run(SWAPS[cid]).stream() if r["type"] == kind and r["date"] == I(date))


def test_a_swap_is_two_loans_one_each_way():
    # swppv04 pays fixed: it borrowed the fixed leg and lent the floating one, in two books
    r = actus.run(SWAPS["swppv04"])
    fixed, floating = r.legs()
    assert fixed.group.book("holder").accounts["swppv04_fixed.notional"].nature is Nature.LIABILITY
    assert floating.group.book("holder").accounts["swppv04_floating.notional"].nature is Nature.ASSET
    # the notionals exchanged at the start and at maturity cancel: the swap pays nothing then
    assert swap_row("swppv04", "2015-01-01T00:00:00", "IED")["payoff"] == 0
    assert swap_row("swppv04", "2016-01-01T00:00:00", "MD")["payoff"] == 0
    for leg in r.legs():
        assert all(v == 0 for t in range(len(leg.plan.instants)) for v in leg.group.book("holder").imbalance(t).values())


def test_a_swap_bought_pays_its_price_alone_and_reports_the_fixed_legs_accrued_interest():
    row = swap_row("swppv12", "2015-03-15T23:59:59", "PRD")
    assert row["payoff"] == -500                                  # the price; no accrued interest on top
    assert row["accrued"] == -F(1000) * F(5, 100) * 15 / 365      # the fixed leg's, 1 to 16 March, signed PF
    # the buyer then receives the floating leg's whole April coupon, from 1 March
    assert swap_row("swppv12", "2015-04-01T00:00:00", "IPFL")["payoff"] == F(1000) * F(5, 100) * 31 / 365


def test_a_swap_settled_net_pays_one_coupon_of_the_difference():
    ip = swap_row("swppv06", "2015-07-01T00:00:00", "IP")
    assert ip["payoff"] == F(1000) * (F(8, 100) - F(5, 100)) * 181 / 365


def test_a_reset_on_the_termination_date_reports_the_notional_still_outstanding():
    # swppv14 resets, then terminates, on 2015-07-01: the reset precedes the termination
    assert swap_row("swppv14", "2015-07-01T00:00:00", "RR")["notional"] == 1000
    assert swap_row("swppv14", "2015-07-01T00:00:00", "TD")["notional"] == 0


def test_a_reset_anchor_with_no_cycle_is_a_single_reset():
    # swppv01: one reset, on the exchange date, to the observation there
    assert swap_row("swppv01", "2015-01-01T00:00:00", "RR")["rate"] == F(1, 100)


def test_a_swap_refuses_what_the_corpus_does_not_show():
    case = copy.deepcopy(SWAPS["swppv02"])
    case["terms"]["contractRole"] = "RPA"
    with pytest.raises(actus.ActusError, match="swap role"):
        actus.run(case)
    case = copy.deepcopy(SWAPS["swppv04"])
    case["terms"]["cycleOfInterestPayment"] = "P6ML1"     # quarterly resets between half-yearly payments
    with pytest.raises(actus.ActusError, match="falls between the fixed leg's events"):
        actus.run(case)
