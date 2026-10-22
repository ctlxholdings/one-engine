"""Shared rulebooks: the rulebooks many kinds of deal declare, filed by the module card they serve.

A rulebook is configuration read from a case file and installed on an
:class:`~one_engine.entities.Group` as a handler for one phase:
``rulebook(group, reader, ctx)``, with ``ctx`` holding the case's currency,
classification schemes, shared asset registers and distributors; it may
return named checks (callables listing failures) that the case runner adds
to the verdicts. Rulebooks hold no arithmetic of their own beyond wiring:
dividing an amount is Allocation, a test is Conditions, a ratio is
Measures, depreciation is Measurement, the base and the loss pool are Tax.

Packs (:mod:`one_engine.packs`) hold what one kind of deal needs. A
rulebook that three or more archetypes declare (``one_engine/archetypes.yaml``)
is not one kind of deal's, so it lives here, one file per module card:

``allocation`` (card 7)
    ``funding_plan``, ``deficit_funding``, ``waterfall``, ``capital_first``:
    who is paid what, in which order, and who funds what.
``measurement`` (card 10)
    ``asset_register``: capitalisation, depreciation and disposal.
``tax`` (card 9)
    ``income_tax``: the charge on the classified base, losses, deferred tax.
``postings`` (card 4, the ledger kernel)
    ``transfer``, ``held_balance``, ``settle``, ``pay_as_due``: amounts the
    case states, posted as stated or settled in full as they fall due.

Why a package beside the cards rather than inside each card's module: the
card's own module is an operator the rulebook calls (``one_engine.tax``
depends only on Cuts, Measurement and the kernel, per the dependency list
in the consolidation's MODULE_CARDS). A rulebook also depends on Entities
and Inputs to install itself; keeping it here keeps the operators free of
that, and keeps the arrows acyclic.

Ali, 2026-10-14, chose this move; ``tests/test_archetypes.py`` fails when a
rulebook that three archetypes declare is left in a pack, and
``tests/test_architecture.py`` when a shared module (these included)
imports a pack.
"""
from one_engine.rulebooks import allocation, measurement, postings, tax

RULEBOOKS = {**allocation.RULEBOOKS, **measurement.RULEBOOKS, **postings.RULEBOOKS, **tax.RULEBOOKS}
