"""one_engine: the consolidated modelling engine.

One engine, built once. Every model (a techno-economic assessment, a project
finance SPV, a fund, a multi-entity structure) is an *assembly*: shared
modules switched on, plus packs (asset generators, contract types,
rulebooks). No model gets its own engine.

The design lives outside this repository, in the project's
``consolidation/`` folder:

* ``MODULE_MAP_DRAFT.md`` — the modules, the invariants (§8) and the
  decisions closed by test (§9);
* ``MODULE_CARDS.md`` — one card per module: its job, inputs, outputs,
  invariants owned, what it must never do, and its first planted-error test.

Modules built so far (plan step 5, build order group 1):

* :mod:`one_engine.units`   — card 1, exact quantities with explicit units;
* :mod:`one_engine.inputs`  — card 2, inputs read once, states, strict intake;
* :mod:`one_engine.kernel`  — card 4, the ledger kernel;
* :mod:`one_engine.checks`  — card 17, the integrity rule set.

This package must not import ``generic_engine`` (the old engine), so that
the two can run side by side on the same fixtures and the old one can be
retired without touching this one. ``tests/test_architecture.py`` enforces
that.
"""
