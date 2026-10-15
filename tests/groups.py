"""The three groups every one_engine test belongs to.

A run of 2,000-odd tests mixes three different questions, and one pass count
hides which is which (Ali, 2026-10-13: the non-financial tests "distort the
things I care about"). Each test is placed in exactly one group:

``answers``
    A receipt against an outside source: the engine's figures compared with an
    oracle it did not write (ACTUS, published reports, golds, esco deals,
    library suites, workbooks). These are the tests named
    ``test_every_..._gets_its_recorded_verdict``; the scoreboard reports the
    same receipts with their verdicts.
``finance``
    The engine's financial behaviour: real cases run through the ledger and its
    laws (``test_case_*``, the whole chain) and the financial rules of each
    module (tax, measures, depreciation, holders, obligations ...), checked
    against hand-derived numbers.
``code``
    The software: readers and parsers, refusals, the oracle harness itself,
    and the mutation guards that prove each check can fail.

A test file not listed here refuses at collection (``KeyError``), so a new file
is placed by a person, never by default. The placement is per file, except in
the oracle files, where the receipts are ``answers`` and every other test is a
test of the harness, so ``code``.
"""
from collections import Counter
from pathlib import Path

TESTS = Path(__file__).parent
GROUPS = ("answers", "finance", "code")

#: files whose every test checks a financial rule or a case (besides ``test_case_*``)
FINANCE = {
    "test_chain.py", "test_kernel.py", "test_checks.py", "test_tax.py", "test_income_tax_options.py",
    "test_measures.py", "test_measures_annuity.py", "test_measurement.py", "test_holders.py",
    "test_obligations.py", "test_allocation.py", "test_portfolio.py", "test_asset_register_carried.py",
    "test_dates.py", "test_entities.py", "test_contracts.py", "test_market.py",
}
#: files whose every test checks the software: readers, refusals, guards
CODE = {
    "test_mutations.py", "test_architecture.py", "test_inputs.py", "test_units.py", "test_cuts.py",
    "test_loops.py", "test_conditions.py", "test_groups.py", "test_archetypes.py", "test_runs.py",
}


def group(filename: str, testname: str) -> str:
    """The group of test ``testname`` in file ``filename``; ``KeyError`` for an unplaced file."""
    if filename.startswith("test_oracles"):
        # a receipt is the oracle's answer; everything else in these files tests the harness
        receipt = testname.startswith("test_every_") and testname.endswith("_gets_its_recorded_verdict")
        return "answers" if receipt else "code"
    if filename.startswith("test_case_") or filename in FINANCE:
        return "finance"
    if filename in CODE:
        return "code"
    raise KeyError(f"{filename} is in no test group: place it in one_engine/tests/groups.py")


def tally(outcomes) -> dict:
    """``{group: {outcome: count}}`` from ``(group, outcome)`` pairs, every group present."""
    counts = {g: Counter() for g in GROUPS}
    for g, outcome in outcomes:
        counts[g][outcome] += 1
    return {g: dict(c) for g, c in counts.items()}
