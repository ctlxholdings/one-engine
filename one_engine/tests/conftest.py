"""Marks each one_engine test with its group and reports the groups apart.

The groups are defined in :mod:`one_engine.tests.groups`. Each collected test
gets a marker of its group, so one group runs alone::

    python3 -m pytest one_engine/tests -m answers     # receipts against outside sources
    python3 -m pytest one_engine/tests -m finance     # cases and financial rules
    python3 -m pytest one_engine/tests -m code        # readers, harness, guards

and every run ends with a line per group (passed / failed / xfailed ...), so
the financial count is never read off a total that includes the code tests.
"""
from pathlib import Path

import pytest

from one_engine.tests.groups import GROUPS, group, tally

_OUTCOMES = []


def pytest_configure(config):
    for g in GROUPS:
        config.addinivalue_line("markers", f"{g}: one_engine test group (see one_engine/tests/groups.py)")


def pytest_collection_modifyitems(config, items):
    for item in items:
        item.add_marker(getattr(pytest.mark, group(Path(item.fspath).name, item.originalname)))


def pytest_runtest_logreport(report):
    # one outcome per test: the call phase, or the setup phase when the test never ran its body
    if report.when == "call" or (report.when == "setup" and not report.passed):
        g = next((k for k in GROUPS if k in report.keywords), None)
        if g is not None:
            outcome = "xfailed" if hasattr(report, "wasxfail") and report.skipped else report.outcome
            _OUTCOMES.append((g, outcome))


def pytest_terminal_summary(terminalreporter):
    if not _OUTCOMES:
        return
    terminalreporter.write_sep("-", "by test group")
    for g, counts in tally(_OUTCOMES).items():
        terminalreporter.write_line(f"{g:8s} " + (", ".join(f"{n} {o}" for o, n in sorted(counts.items())) or "none run"))
