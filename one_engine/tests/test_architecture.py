"""The new engine must not lean on the old one, and its shared modules must not lean on a pack.

Plan step 8 runs generic_engine and one_engine side by side on the same
fixtures; the old engine is retired afterwards. If one_engine imported
generic_engine, retiring the old engine would break the new one, and a
side-by-side comparison would partly compare the old engine with itself.

Packs hold what differs between kinds of deal (``packs/__init__``): "shared
modules never import a pack; an assembly (a case file) names the packs it
uses". A shared module that imported a pack would make every archetype
depend on that pack's deal type. Only the assembly (``cases``), the oracle
harness, the archetype tracer and the tests may import packs, and packs may
build on each other.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = ("generic_engine", "_generic_engine", "vendor")
#: the parts of the engine that may import a pack: the packs, the assembly, the oracle harness, the tracer, the tests
MAY_IMPORT_PACKS = ("packs", "cases", "oracles", "tests", "archetypes.py")


def _imports(path):
    """``(line, dotted name)`` of every import in a file, function-level ones included."""
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        if isinstance(node, ast.Import):
            yield from ((node.lineno, a.name) for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.lineno, node.module
            if node.module == "one_engine":
                yield from ((node.lineno, f"one_engine.{a.name}") for a in node.names)


def test_one_engine_imports_nothing_from_the_old_engines():
    offenders = []
    for path in ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in FORBIDDEN:
                    offenders.append(f"{path.relative_to(ROOT)}:{node.lineno} {name}")
    assert not offenders, "one_engine imports an old engine:\n" + "\n".join(offenders)


def test_a_shared_module_imports_no_pack():
    offenders = []
    for path in ROOT.rglob("*.py"):
        if path.relative_to(ROOT).parts[0] in MAY_IMPORT_PACKS:
            continue
        offenders += [f"{path.relative_to(ROOT)}:{line} {name}" for line, name in _imports(path)
                      if name.startswith("one_engine.packs")]
    assert not offenders, "a shared module imports a pack:\n" + "\n".join(offenders)


def test_each_shared_piece_lives_where_it_was_decided():
    # Ali, 2026-10-15 ("Move them"): the formulas generator (ten archetypes) beside Inputs, the loop rules
    # (three) beside the loop solver, and tax instalments (used by no project-finance case) beside income tax
    from one_engine.cases import GENERATORS, LOOP_RULES, RULEBOOKS
    homes = {"formulas": GENERATORS["formulas"].__module__, "tax_instalments": RULEBOOKS["tax_instalments"].__module__}
    homes.update({f"loop rule {k}": f.__module__ for k, f in LOOP_RULES.items()})
    wrong = {k: v for k, v in homes.items()
             if v != {"formulas": "one_engine.formulas", "tax_instalments": "one_engine.rulebooks.tax"}.get(k, "one_engine.rulebooks.loops")}
    assert wrong == {}
