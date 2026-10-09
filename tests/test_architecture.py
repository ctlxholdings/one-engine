"""The new engine must not lean on the old one.

Plan step 8 runs generic_engine and one_engine side by side on the same
fixtures; the old engine is retired afterwards. If one_engine imported
generic_engine, retiring the old engine would break the new one, and a
side-by-side comparison would partly compare the old engine with itself.
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
FORBIDDEN = ("generic_engine", "_generic_engine", "vendor")


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
