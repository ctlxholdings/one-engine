"""Every top-level case read through the McKinsey valuation lens (W9, 2026-10-18).

What this file holds
--------------------
The valuation pack's lens (:func:`one_engine.packs.valuation.valuation`)
reorganizes one entity's books into the book's operating quantities: invested
capital, NOPLAT, free cash flow, ROIC and, where a discount rate is given,
economic profit (*Valuation*, 5th ed., ch. 7). It reads the books under a
classification scheme that puts every money account in one class
(``CLASSES``: operating, nonoperating or financing) and refuses an account the
scheme leaves out.

Each case file directly in ``one_engine/cases`` (``baghara.yaml`` is a process
network, not a case, and is left out) now declares:

* a scheme named ``valuation`` classifying every money account of every one of
  its books; the same account name in two books has one class, so one scheme
  serves them all;
* one lens per book, ``valuation`` (a single book) or ``valuation_<entity>``,
  with ``roic_on: opening``, the entity's own income-tax rate as its marginal
  rate (0 where the case has no income tax), and a ``wacc`` only where the case
  itself holds a discount rate for that entity (an npv measure's rate).

What is held here, for every such case:

* each book has its lens, and each lens names a book of the case;
* uses equal sources exactly in every period (total funds invested against
  total funds sourced). The lens computes both sides from the same balances,
  so a classification that is complete always balances; the check pins that
  no account is read on the wrong side or twice;
* NOPLAT, invested capital, FCF and ROIC exist, and economic profit exists
  exactly where the lens has a WACC;
* the lens's marginal tax rate is the case's income_tax rulebook rate for
  that entity, value for value (or 0 where the entity has none), so the
  lens never taxes NOPLAT at a rate the books do not use.

And one refusal: a case whose scheme drops one account does not run (CutError),
so a scheme cannot fall behind its books silently.

Each case is run once (cached); the whole file runs every top-level case.
"""
import copy
import functools
from fractions import Fraction

import pytest

from one_engine.cases import CASES, run_case
from one_engine.cuts import CutError
from one_engine.inputs import load_yaml_strict
from one_engine.packs.valuation import _MONEY
from one_engine.units import exact

#: the top-level case files; baghara.yaml is a process network that run_case refuses
STEMS = sorted(p.stem for p in CASES.glob("*.yaml") if p.stem != "baghara")


@functools.lru_cache(maxsize=None)
def doc(stem: str):
    """The case file, parsed as the runner parses it."""
    return load_yaml_strict((CASES / f"{stem}.yaml").read_text())


@functools.lru_cache(maxsize=None)
def result(stem: str):
    """The case's run (the lenses included)."""
    return run_case(CASES / f"{stem}.yaml")


def valuation_lenses(stem: str):
    """The case's valuation lenses: name -> its block in the file."""
    return {k: v for k, v in (doc(stem).get("lenses") or {}).items() if v.get("kind") == "valuation"}


def income_tax_rate(stem: str, entity: str):
    """The rate of the entity's income_tax rulebook, as written; None if the entity has none."""
    rates = [rb["rate"] for rb in (doc(stem).get("rulebooks") or {}).values()
             if rb.get("kind") == "income_tax" and rb.get("entity") == entity]
    assert len(rates) <= 1, f"{stem}: {entity} has {len(rates)} income_tax rulebooks"
    return rates[0] if rates else None


def as_exact(rate):
    """A rate stated once or period by period, as exact numbers (a list stays a list)."""
    return [exact(x) for x in rate] if isinstance(rate, list) else exact(rate)


@pytest.mark.parametrize("stem", STEMS)
def test_each_book_has_one_valuation_lens(stem):
    books = {e for e, b in result(stem).group.books.items()
             if any(a.nature in _MONEY for a in b.accounts.values())}
    lenses = valuation_lenses(stem)
    assert sorted(v["entity"] for v in lenses.values()) == sorted(books), stem
    for name, v in lenses.items():
        assert name == ("valuation" if len(books) == 1 else f"valuation_{v['entity']}"), name
        assert v["scheme"] == "valuation" and v["roic_on"] == "opening", name


@pytest.mark.parametrize("stem", STEMS)
def test_uses_equal_sources_in_every_period(stem):
    res = result(stem)
    for name in valuation_lenses(stem):
        uses = res.cuts[f"{name}.total_funds_invested"].values
        sources = res.cuts[f"{name}.total_funds_sources"].values
        assert len(uses) == len(sources) > 0, name
        gaps = {t: u - s for t, (u, s) in enumerate(zip(uses, sources)) if u != s}
        assert not gaps, f"{stem}/{name}: uses differ from sources at {gaps}"


@pytest.mark.parametrize("stem", STEMS)
def test_the_operating_quantities_are_read(stem):
    res = result(stem)
    for name, v in valuation_lenses(stem).items():
        for series in ("noplat", "invested_capital", "fcf", "roic"):
            assert f"{name}.{series}" in res.cuts, f"{stem}/{name}: no {series}"
        # economic profit needs a WACC; the lens reports it exactly when the case gives one
        assert (f"{name}.economic_profit" in res.cuts) == ("wacc" in v), f"{stem}/{name}"


@pytest.mark.parametrize("stem", STEMS)
def test_the_marginal_rate_is_the_entity_s_income_tax_rate(stem):
    for name, v in valuation_lenses(stem).items():
        rate = income_tax_rate(stem, v["entity"])
        expected = Fraction(0) if rate is None else as_exact(rate)
        assert as_exact(v["marginal_tax_rate"]) == expected, f"{stem}/{name}"


def test_only_time_is_money_holds_a_discount_rate():
    # the npv measure's rate is the one discount rate a top-level case holds (10% a year, on a yearly grid)
    with_wacc = {(s, n): exact(v["wacc"]) for s in STEMS for n, v in valuation_lenses(s).items() if "wacc" in v}
    assert with_wacc == {("modeloff_time_is_money", "valuation"): Fraction(1, 10)}
    npv = doc("modeloff_time_is_money")["measures"]["npv_10"]
    assert npv["measure"] == "npv" and exact(npv["rate"]) == exact(with_wacc[("modeloff_time_is_money", "valuation")])


def test_a_scheme_missing_an_account_refuses():
    case = copy.deepcopy(doc("eria"))
    del case["schemes"]["valuation"]["equipment"]
    with pytest.raises(CutError, match="does not classify: equipment"):
        run_case(case)
