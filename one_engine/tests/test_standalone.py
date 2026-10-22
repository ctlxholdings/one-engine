"""The engine stands alone: one repository, installable, nothing read from outside it.

Why this file exists
--------------------
one_engine was built inside generic-framework, beside the old engine and a
lot of unrelated tooling, and some of its referee data was read from there
(the nightmare fixture sat in ``generic_engine/tests/fixtures``). Ali asked
for the engine in one clean place, consumed by other projects (esco-product
first) as a pinned package. Two things must then hold, and both are checked
here so that a regression turns red instead of surfacing in a consumer:

1. **Nothing outside the repository is read.** No module or test builds a
   path into a sibling project (``generic_engine``, ``consolidation``,
   ``vendor``). Referee data lives under ``one_engine/cases/reference``.
2. **The installed package runs.** A wheel built from this repository,
   installed into a fresh virtual environment with only its declared
   dependencies, imports and runs a case from outside the source tree. This
   is the check that ``pyproject.toml`` ships the YAML the engine reads
   (``archetypes.yaml``, the case files) and declares every runtime import.

The second test needs ``pip`` to reach a package index (for PyYAML and the
build backend); without one it is skipped, and says so.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys
import textwrap

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1]
REPO = PKG.parent

# A path segment naming a sibling project of the old home (generic_engine,
# consolidation, vendor) joined with ``/`` inside a path expression. Docstrings that only
# *cite* where a reference was first written (``consolidation/experiments/...``)
# are provenance, not reads, so only path-building forms are matched.
SIBLING = re.compile(r'/\s*["\'](generic_engine|_generic_engine|consolidation|vendor)["\']')


def test_no_path_leaves_the_repository():
    offenders = []
    for py in PKG.rglob("*.py"):
        if "reference" in py.relative_to(PKG).parts[:2]:
            continue                                   # vendored oracles, read as data
        for n, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if SIBLING.search(line):
                offenders.append(f"{py.relative_to(REPO)}:{n}: {line.strip()}")
    assert not offenders, "paths into sibling projects:\n" + "\n".join(offenders)


def test_installed_package_runs_a_case_outside_the_repo(tmp_path):
    venv = tmp_path / "venv"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    py = venv / "bin" / "python"
    install = subprocess.run([str(py), "-m", "pip", "install", "-q", str(REPO)],
                             capture_output=True, text=True)
    if install.returncode and ("Could not find" in install.stderr or "connection" in install.stderr.lower()):
        pytest.skip("no package index reachable: " + install.stderr.strip().splitlines()[-1])
    assert install.returncode == 0, install.stderr
    # Run from tmp_path so the source tree is not on sys.path: only the
    # installed copy can answer. ERIA's provider income is its published
    # 266,161.51 a year (tests/test_case_eria.py).
    script = textwrap.dedent("""
        import pathlib, one_engine
        from one_engine.cases import run_case
        from one_engine import archetypes
        root = pathlib.Path(one_engine.__file__).parent
        assert "site-packages" in str(root), root
        r = run_case(root / "cases" / "eria.yaml")
        print(round(float(r.cut("provider_project_cash_flow").values[1]), 2))
    """)
    out = subprocess.run([str(py), "-c", script], cwd=tmp_path, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "266161.51"
