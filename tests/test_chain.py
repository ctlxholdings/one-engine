"""The whole chain, from each case file down to the ledger, on every case file (bottom-up integration).

The engine is built in layers: the ledger (kernel), contracts and their
claims, the rulebooks that pay them (funding, waterfall, promote), the case
runner, and cases that read other cases (Averta's TopCo reads its GP, which
reads the HoldCo, which reads 1,260 SPVs and the revenue engine). Each
layer was added on top of a tested one, and every case test runs the whole
chain. This file enforces that, independently of each case's own test:

1. Every case file in ``one_engine/cases`` runs end to end into the ledger,
   and every integrity check on every book is run and passes. A new case
   file is picked up without being listed, and it must also be named by a
   test file that holds it to its referee.
2. A fault planted in the ledger must turn the top of the chain red (the
   ``test_mutations.py`` entries marked "the chain"), not only the kernel's
   own tests. Doing so showed that nothing compared the journal (the
   postings, which cash cuts read) with the balances: every case run here
   now reconciles the two.
3. What a rulebook records as paid (its side notes, which some case tests
   read) is what the ledger holds: every waterfall tier, funding draw and
   residual, promote tier and shortfall call is matched to its postings by
   event id, in every case. Records that are not amounts posted (DSCR, the
   tax loss pool, the tax NBV) are measures over the ledger, not tied here.
"""
import pathlib
from fractions import Fraction as F

import pytest

from one_engine.cases import run_case
from one_engine.inputs import load_yaml_strict
from one_engine.packs.process import load as load_network

HERE = pathlib.Path(__file__).resolve().parents[1] / "cases"
TESTS = pathlib.Path(__file__).resolve().parent
CASE_FILES = sorted(p.name for p in HERE.glob("*.yaml"))
CHECKS = {"roll_forward", "carry_forward", "balance", "bounds", "wind_down"}


def is_network(name):
    return "network" in load_yaml_strict((HERE / name).read_text())


@pytest.fixture(scope="module")
def runs():
    """Each financial case file, run once."""
    return {n: run_case(HERE / n) for n in CASE_FILES if not is_network(n)}


# ── 1. every case file, end to end ────────────────────────────────────────

def test_the_case_directory_holds_the_cases():
    assert len(CASE_FILES) >= 27 and "averta_holdco_fixed.yaml" in CASE_FILES


@pytest.mark.parametrize("name", CASE_FILES)
def test_every_case_file_runs_end_to_end_into_the_ledger(name, runs):
    if is_network(name):
        # a process network (Baghara's mass balance) posts a year into one book of the same kernel
        from one_engine.checks import integrity
        run = load_network(HERE / name).plan(50).post()
        verdicts = integrity(run.book.table(), run.book.accounts)
        assert {v.name for v in verdicts} >= CHECKS - {"wind_down"}
        assert [v for v in verdicts if not v.passed] == []
        assert run.unclosed() == {}                  # every process conserves; stranded streams are its findings
        return
    r = runs[name]
    assert r.group.books, name
    for entity, verdicts in r.verdicts.items():
        assert {v.name for v in verdicts} == CHECKS, (name, entity)
    assert r.failed_checks() == [], name
    assert any(b.postings for b in r.group.books.values()), name
    for entity, book in r.group.books.items():
        assert journal_is_ledger(book), (name, entity)


def journal_is_ledger(book):
    """Every balance's movement is the sum of the postings to it, in every period.

    The kernel keeps both the journal (the postings, which cash cuts read)
    and the balances (which contracts, rulebooks and the integrity checks
    read). A fault that makes them disagree is invisible to the integrity
    checks, which run on the balances, and to a case read only through its
    cuts (ERIA): this is where it shows.
    """
    moves = {}
    for p in book.postings:
        for acct in (p.debit, p.credit):
            moves[(acct, p.period)] = moves.get((acct, p.period), F(0)) + p.effect[acct] * p.amount
    return all(book.balance(a, t) - book.opening(a, t) == moves.get((a, t), F(0))
               for a in book.accounts for t in range(book.start_period, book.periods))


@pytest.mark.parametrize("name", CASE_FILES)
def test_every_case_file_is_held_to_a_referee_by_a_test(name):
    users = [p.name for p in TESTS.glob("test_case_*.py") if name in p.read_text()]
    assert users, f"{name} is run by no case test: nothing holds its numbers to a referee"


# ── 3. side notes are the ledger ──────────────────────────────────────────

def posted(r, event, phase):
    """The amount posted under ``event`` in ``phase`` in any book, its withheld part included.

    The phase matters: a claim may be paid by two rulebooks in one period
    (construction interest funded as a use in ``fund``, nothing left for the
    waterfall in ``pay``).
    """
    return sum((p.amount for b in r.group.books.values() for p in b.postings
                if p.phase == phase and (p.event == event or p.event == f"{event}:withheld")), F(0))


def claim_event(ref, t):
    c, name = ref.split(".", 1)
    return f"{c}:claim.{name}:{t}"


def residual_legs(raw):
    return [(raw, F(1))] if isinstance(raw, str) else [(x["leg"], F(x["share"])) for x in raw]


def leg_posted(r, leg, t, phase):
    c, name = leg.split(".", 1)
    return posted(r, f"{c}:leg.{name}:{t}:{phase}", phase)


def rulebooks(name, kind):
    case = load_yaml_strict((HERE / name).read_text())
    return {k: v for k, v in case.get("rulebooks", {}).items() if v.get("kind") == kind}


@pytest.mark.parametrize("name", [n for n in CASE_FILES if rulebooks(n, "waterfall")])
def test_every_waterfall_payment_it_records_is_in_the_ledger(name, runs):
    r = runs[name]
    (w,) = rulebooks(name, "waterfall").values()
    tiers = {}
    for spec in w["tiers"]:
        if "claim" in spec:
            tiers[spec["claim"]] = ("claim", spec["claim"])
        elif "keep" in spec:
            tiers[spec["name"]] = ("keep", None)          # a minimum balance: what it is "paid" stays in cash
        else:
            tiers[spec["name"]] = ("account", spec["account"])
    checked = 0
    for t, rec in r.records("waterfall").items():
        for tier, amount in rec["paid"].items():
            kind, ref = tiers[tier]
            if kind == "keep":
                continue
            event = claim_event(ref, t) if kind == "claim" else f"waterfall.{tier}:{t}"
            ledger = posted(r, event, w["phase"])
            assert ledger == amount, (name, t, tier, amount, ledger)
            checked += amount != 0
    assert checked, name


@pytest.mark.parametrize("name", [n for n in CASE_FILES if rulebooks(n, "funding_plan")])
def test_every_funding_draw_and_call_it_records_is_in_the_ledger(name, runs):
    r = runs[name]
    plans = list(rulebooks(name, "funding_plan").values())
    for t, rec in r.records("funding").items():
        (f,) = [x for x in plans if int(x.get("from", 0)) <= t <= int(x["until"])]
        legs = residual_legs(f["residual"])
        for ref, amount in rec["paid"].items():
            assert posted(r, claim_event(ref, t), f["phase"]) == amount, (name, t, ref)
        called = sum((leg_posted(r, leg, t, f["phase"]) for leg, _ in legs), F(0))
        assert called == rec["residual"], (name, t)


@pytest.mark.parametrize("name", [n for n in CASE_FILES if rulebooks(n, "promote")])
def test_every_promote_tier_it_records_is_in_the_ledger(name, runs):
    r = runs[name]
    (p,) = rulebooks(name, "promote").values()
    (w,) = rulebooks(name, "waterfall").values()
    carry, phase = F(str(p["carry_rate"])), w["phase"]
    for t, rec in r.records("promote").items():
        assert leg_posted(r, f"{p['lp']}.return_capital", t, phase) == rec["roc_lp"]
        assert leg_posted(r, f"{p['gp']}.return_capital", t, phase) == rec["roc_gp"]
        assert leg_posted(r, f"{p['lp']}.distribute", t, phase) == rec["pref"] + rec["residual"] * (1 - carry)
        assert leg_posted(r, f"{p['gp']}.distribute", t, phase) == rec["catchup"] + rec["residual"] * carry


@pytest.mark.parametrize("name", [n for n in CASE_FILES if rulebooks(n, "deficit_funding")])
def test_every_shortfall_call_it_records_is_in_the_ledger(name, runs):
    r = runs[name]
    for rname, d in rulebooks(name, "deficit_funding").items():
        recs = r.records(rname)
        assert recs, (name, rname)
        for t, rec in recs.items():
            got = sum((leg_posted(r, leg, t, d["phase"]) for leg, _ in residual_legs(d["residual"])), F(0))
            assert got == rec["called"], (name, rname, t)
