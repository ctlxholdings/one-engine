"""Deal archetypes: which kinds of deal the engine models, and which modules each may use.

Ali (2026-10-13): "have a archetype of deals we model and what modules they
should be using, like an architecture". ``archetypes.yaml`` beside this file
is that architecture, as data. Each archetype (coded as in the consolidation's
MODELING_TAXONOMY, e.g. ``D1`` project finance) lists:

* ``cases``: the cases that are instances of it, as patterns over case ids;
* ``modules``: the engine modules its runs may call (the shared modules of
  the module cards, ``kernel``, ``tax``, ``measures`` ..., and the packs,
  ``packs.pf``, ``packs.actus`` ...);
* ``rulebooks``, ``contracts``, ``generators``: the kinds its case files may
  declare (the assembly a case switches on).

A **case id** names one runnable case: ``case/<stem>`` for a file in
``one_engine/cases``, ``panel/<stem>`` for an esco panel base, and
``<family>/<oracle>`` for the translation of one oracle (``published/...``,
``golds/...``, ``actus/pam/pam01`` ...). Every case is placed in exactly one
archetype; a case placed in none refuses (``KeyError``), so a new case is
placed by a person.

This module only reads: it loads the registry, enumerates the cases, reads
what a case declares, and traces which modules a case's run calls
(:func:`modules_used`, a profiler hook on calls, so it sees what ran, not
what was imported). ``tests/test_archetypes.py`` holds every case to its
archetype. The registry's lists were first written from what the cases
were measured to use (2026-10-13), so they record the architecture as
built; a case that later needs more is either a different archetype or a
recorded decision to grow one.
"""
import fnmatch
import importlib
import pathlib
import sys
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set

import yaml

from one_engine.inputs import load_yaml_strict

HERE = pathlib.Path(__file__).resolve().parent
CASES = HERE / "cases"
REGISTRY_PATH = HERE / "archetypes.yaml"
KINDS = ("rulebooks", "contracts", "generators")
#: engine folders that are not modules a case selects: the oracle harness, the tests, and the
#: case files with the run driver (``cases/__init__``), which every case uses
NOT_MODULES = ("oracles", "tests", "cases")
#: a rulebook declared by this many archetypes is shared: it lives in a shared module, not a pack
SHARED_AT = 3
#: case-id prefix of each oracle family, with the folder of its translations
FAMILY_FOLDERS = {"golds": "golds", "pilot": "golds_pilot", "published": "published", "esco": "esco",
                  "external": "external", "workbook": "workbooks"}


def load(path: pathlib.Path = REGISTRY_PATH) -> Dict[str, Dict[str, Any]]:
    """The archetypes by code, each with its name, case patterns and the lists it allows."""
    return yaml.safe_load(path.read_text())["archetypes"]


def engine_modules() -> Set[str]:
    """Every engine module a case may call, by dotted name under ``one_engine`` (``kernel``, ``packs.pf``)."""
    out = set()
    for p in HERE.rglob("*.py"):
        rel = p.relative_to(HERE)
        if rel.parts[0] in NOT_MODULES or p.name == "__init__.py" or p.name == "archetypes.py":
            continue
        out.add(".".join(rel.with_suffix("").parts))
    return out


def case_ids() -> List[str]:
    """Every runnable case: the case files, the esco panel bases, every family translation, every ACTUS test."""
    from one_engine.oracles.actus import ActusFamily
    ids = [f"case/{p.stem}" for p in sorted(CASES.glob("*.yaml"))]
    ids += [f"panel/{p.stem}" for p in sorted((CASES / "panel").glob("*.yaml"))]
    for prefix, folder in FAMILY_FOLDERS.items():
        ids += [f"{prefix}/{p.stem}" for p in sorted((CASES / folder).glob("*.yaml"))]
    ids += [f"actus/{oid}" for oid in ActusFamily().oracles()]
    return ids


def archetype_of(case: str, registry: Mapping[str, Mapping[str, Any]]) -> str:
    """The code of the one archetype whose patterns match ``case``.

    Raises ``KeyError`` when none does (an unplaced case) and ``ValueError``
    when several do (a case cannot be two kinds of deal).
    """
    hits = [code for code, a in registry.items() if any(fnmatch.fnmatchcase(case, p) for p in a["cases"])]
    if not hits:
        raise KeyError(f"{case} is in no archetype: place it in one_engine/archetypes.yaml")
    if len(hits) > 1:
        raise ValueError(f"{case} is in several archetypes: {hits}")
    return hits[0]


def _doc(case: str) -> Optional[Mapping[str, Any]]:
    """The case document a case id runs (the ``case`` block of a translation), or None (ACTUS)."""
    prefix, _, stem = case.partition("/")
    if prefix == "case":
        return load_yaml_strict((CASES / f"{stem}.yaml").read_text())
    if prefix == "panel":
        return load_yaml_strict((CASES / "panel" / f"{stem}.yaml").read_text())
    if prefix in FAMILY_FOLDERS:
        doc = load_yaml_strict((CASES / FAMILY_FOLDERS[prefix] / f"{stem}.yaml").read_text())
        return doc["case"] if isinstance(doc.get("case"), Mapping) else doc
    return None


def declared_kinds(case: str) -> Dict[str, List[str]]:
    """The rulebook, contract and generator kinds a case declares (a contract's kind is its ``type``)."""
    doc = _doc(case) or {}
    out: Dict[str, List[str]] = {}
    for section in KINDS:
        found = {v.get("kind") or v.get("type") for v in (doc.get(section) or {}).values() if isinstance(v, Mapping)}
        out[section] = sorted(k for k in found if k)
    return out


def _runner(case: str) -> Callable[[], Any]:
    """A function that runs the case as its test does."""
    prefix, _, stem = case.partition("/")
    if prefix in ("case", "panel"):
        path = CASES / (f"{stem}.yaml" if prefix == "case" else f"panel/{stem}.yaml")
        if "network" in load_yaml_strict(path.read_text()):
            # a process network (Baghara's mass balance) is planned, posted and checked, not run_case'd
            from one_engine.checks import integrity
            from one_engine.packs.process import load as load_network

            def network():
                r = load_network(path).plan(50).post()
                integrity(r.book.table(), r.book.accounts)
            return network
        from one_engine.cases import run_case
        return lambda: run_case(path)
    from one_engine.oracles.actus import ActusFamily
    from one_engine.oracles.esco import EscoFamily
    from one_engine.oracles.external import ExternalFamily
    from one_engine.oracles.golds import GoldFamily
    from one_engine.oracles.pilot import PilotFamily
    from one_engine.oracles.published import PublishedFamily
    from one_engine.oracles.workbook import WorkbookFamily
    family = {"actus": ActusFamily, "golds": GoldFamily, "pilot": PilotFamily, "published": PublishedFamily,
              "esco": EscoFamily, "external": ExternalFamily, "workbook": WorkbookFamily}[prefix]()
    return lambda: family.run(stem)


def _module(filename: str) -> Optional[str]:
    try:
        rel = pathlib.Path(filename).resolve().relative_to(HERE)
    except ValueError:
        return None
    # this module's own frames (the runner lambdas) are the harness, never a module the case selected
    if rel.parts[0] in NOT_MODULES or rel.suffix != ".py" or rel.name in ("__init__.py", "archetypes.py"):
        return None
    return ".".join(rel.with_suffix("").parts)


@lru_cache(maxsize=None)
def _import_all() -> None:
    """Import every engine module once, before any trace.

    Some modules are imported lazily, inside a function, the first time a
    case needs them; importing runs the module's body and any calls it
    makes at import. Traced, those would count for whichever case happened
    to import the module first, so a case's list would depend on the order
    cases run in. With everything imported up front, a trace sees only
    what the run calls.
    """
    for m in sorted(engine_modules()):
        importlib.import_module(f"one_engine.{m}")


def modules_used(case: str) -> List[str]:
    """The engine modules a case's run calls, traced while it runs.

    A run the engine refuses as not built yet (an ACTUS type with no
    mechanism: :class:`one_engine.oracles.Blocked`) still names the modules
    it called before refusing; one refused before it called any module has
    nothing to trace, and its ``Blocked`` is raised so the caller can say so
    (an empty list would read as a run that needs no module). Any other
    error is the case's and is raised.
    """
    from one_engine.oracles import Blocked
    _import_all()
    seen: Set[str] = set()
    cache: Dict[str, Optional[str]] = {}

    def hook(frame, event, arg):
        # calls only: a module counts when one of its functions runs, never for being imported. Importing a
        # module runs its body as a "<module>" call, so without this a module first imported during a case
        # would count for that case only, and the lists would depend on the order cases run in
        if event == "call" and frame.f_code.co_name != "<module>":
            f = frame.f_code.co_filename
            if f not in cache:
                cache[f] = _module(f)
            if cache[f]:
                seen.add(cache[f])

    run = _runner(case)
    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        run()
    except Blocked:
        if not seen:
            raise
    finally:
        sys.setprofile(previous)
    return sorted(seen)


def outside(case: str, kinds: Optional[Mapping[str, Iterable[str]]] = None, modules: Optional[Iterable[str]] = None,
            registry: Optional[Mapping[str, Mapping[str, Any]]] = None) -> List[str]:
    """What ``case`` uses that its archetype does not list: ``module X``, then ``<kind section> X``."""
    registry = registry or load()
    a = registry[archetype_of(case, registry)]
    out = [f"module {m}" for m in sorted(set(modules or ()) - set(a["modules"]))]
    for section in KINDS:
        out += [f"{section} {k}" for k in sorted(set((kinds or {}).get(section, ())) - set(a[section]))]
    return out


def misfiled(registry: Mapping[str, Mapping[str, Any]], rulebooks: Mapping[str, Callable]) -> List[str]:
    """Rulebooks that :data:`SHARED_AT` or more archetypes declare but that still live in a pack.

    A pack holds what one kind of deal needs (``packs/__init__``); a rulebook
    many kinds of deal declare is shared, and belongs beside its module card
    (``one_engine.rulebooks``). Ali, 2026-10-14, chose to move them.
    """
    users: Dict[str, Set[str]] = {}
    for code, a in registry.items():
        for k in a["rulebooks"]:
            users.setdefault(k, set()).add(code)
    out = []
    for k in sorted(users):
        home = rulebooks[k].__module__.replace("one_engine.", "")
        if len(users[k]) >= SHARED_AT and home.startswith("packs."):
            out.append(f"{k} ({home}, {len(users[k])} archetypes)")
    return out
