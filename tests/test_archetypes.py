"""Every case is an instance of one deal archetype, and uses only that archetype's modules.

Ali (2026-10-13): "have a archetype of deals we model and what modules they
should be using, like an architecture". The archetypes and what each may use
are declared in ``one_engine/archetypes.yaml``; :mod:`one_engine.archetypes`
reads it. These tests hold every case to it:

* every case (the case files, the esco panel, and every translation of every
  oracle family) is placed in exactly one archetype, and a case nobody placed
  refuses;
* every rulebook, contract type and generator a case declares is one its
  archetype lists, read from the case file (cheap, every case);
* every engine module a case's run calls is one its archetype lists, traced
  while the case runs (every case but the workbooks, whose runs are the
  slowest and are already held by ``test_oracles_workbook.py``; their
  declared kinds are still checked; an ACTUS type with no mechanism yet
  runs nothing and is skipped, named as not built);
* each list names only modules and kinds the engine has, so a typo cannot
  widen an archetype silently; and each check is seen failing on a planted
  case that steps outside its archetype.

A new case of a known archetype that needs a module its archetype does not
list fails here: either the case is a different archetype, or the archetype
grows, and either is a decision recorded in the registry, not a silent change.
"""
import pytest

from one_engine import archetypes as A
from one_engine.cases import GENERATORS, REGISTRY, RULEBOOKS
from one_engine.oracles import Blocked

REG = A.load()
CASES = A.case_ids()
TRACED = [c for c in CASES if not c.startswith("workbook/")]


def test_the_registry_names_only_what_the_engine_has():
    for code, a in REG.items():
        assert set(a["modules"]) <= A.engine_modules(), code
        assert set(a["rulebooks"]) <= set(RULEBOOKS), code
        assert set(a["contracts"]) <= set(REGISTRY), code
        assert set(a["generators"]) <= set(GENERATORS), code


def test_every_case_is_placed_in_exactly_one_archetype():
    assert len(CASES) > 400
    for c in CASES:
        assert A.archetype_of(c, REG) in REG, c


def test_a_case_nobody_placed_refuses():
    with pytest.raises(KeyError, match="published/new_thing"):
        A.archetype_of("published/new_thing", REG)


@pytest.mark.parametrize("case", CASES)
def test_a_case_declares_only_what_its_archetype_lists(case):
    assert A.outside(case, kinds=A.declared_kinds(case), registry=REG) == [], case


@pytest.mark.parametrize("case", TRACED)
def test_a_case_runs_only_the_modules_its_archetype_lists(case):
    try:
        used = A.modules_used(case)
    except Blocked as why:
        # an ACTUS type the engine has no mechanism for yet: nothing ran, so nothing to hold
        pytest.skip(f"not built yet: {why}")
    assert used, case
    assert A.outside(case, modules=used, registry=REG) == [], case


def test_a_case_stepping_outside_its_archetype_is_seen():
    # a farm budget (B1) that declares the project-finance waterfall, and runs the ACTUS pack
    kinds = {"rulebooks": ["transfer", "waterfall"], "contracts": ["offtake"], "generators": ["formulas"]}
    got = A.outside("published/ext_corn_iowa2025", kinds=kinds, modules=["kernel", "packs.actus"], registry=REG)
    assert got == ["module packs.actus", "rulebooks waterfall"]


def test_a_traced_run_names_the_modules_it_calls():
    used = A.modules_used("case/eria")
    assert {"kernel", "contracts", "measures"} <= set(used) and "packs.actus" not in used
