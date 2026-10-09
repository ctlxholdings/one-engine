"""Every kernel and intake check must be seen failing.

A check that has never failed is indistinguishable from one that cannot
(golds shipped three such gates before it adopted this rule). Each mutation
below switches off one check in a copy of the package and requires that at
least one named test then fails. If a mutation leaves every test green, the
test that should guard that check does not, and this file fails.
"""
import pathlib
import shutil
import subprocess
import sys

import pytest

PKG = pathlib.Path(__file__).resolve().parents[1]

# (file, exact source text, replacement, test that must then fail)
MUTATIONS = [
    ("kernel.py", "if natural != p.effect[acct.name]:", "if False:",
     "test_kernel.py::test_capitalised_interest_that_shrinks_the_debt_refuses"),
    ("kernel.py", "            if bal < acct.floor:", "            if False:",
     "test_kernel.py::test_herd_cannot_lose_more_heads_than_it_has"),
    ("kernel.py", "        if debit.unit != credit.unit:", "        if False:",
     "test_kernel.py::test_unit_mismatch_refuses"),
    ("kernel.py", "        if idx < self._phase:\n            raise KernelError(f\"posting",
     "        if False:\n            raise KernelError(f\"posting",
     "test_kernel.py::test_the_clock_does_not_go_back"),
    ("kernel.py", "        if bad:\n            raise KernelError(f\"opening", "        if False:\n            raise KernelError(f\"opening",
     "test_kernel.py::test_unbalanced_opening_state_refuses"),
    ("units.py", "        return Fraction(value)            # exact", "        return Fraction(value).limit_denominator(10**6)  # exact",
     "test_units.py::test_exact_keeps_every_float_bit"),
    ("units.py", "            raise UnitError(f\"cannot combine", "            return None\n            raise UnitError(f\"cannot combine",
     "test_units.py::test_adding_two_currencies_refuses"),
    ("inputs.py", "        if unread:", "        if False:",
     "test_inputs.py::test_unknown_key_refuses_at_finish"),
    ("inputs.py", "            if key in seen:", "            if False:",
     "test_inputs.py::test_a_key_written_twice_in_the_source_refuses"),
    ("checks.py", "if abs(r.opening + r.movement - r.closing) > tol]", "if False]",
     "test_checks.py::test_a_closing_that_is_not_opening_plus_movement_fails_roll_forward"),
    ("checks.py", "if abs(n.opening - p.closing) > tol]", "if False]",
     "test_checks.py::test_an_opening_that_is_not_last_closing_fails_carry_forward"),
    ("checks.py", "for (u, t), v in sorted(sums.items()) if abs(v) > tol]", "for (u, t), v in sorted(sums.items()) if False]",
     "test_checks.py::test_a_one_sided_edit_fails_the_balance_identity"),
    ("checks.py", "r.closing < accounts[r.account].floor - tol]", "False]",
     "test_checks.py::test_a_balance_below_its_floor_fails_bounds"),
    ("checks.py", "if by_acct.get(a) and abs(by_acct[a][-1].closing) > tol]", "if False]",
     "test_checks.py::test_wind_down_names_what_is_left_standing"),

    ("measures.py", "    if len(roots) > 1:", "    if False:",
     "test_measures.py::test_irr_refuses_when_two_rates_fit"),
    ("cuts.py", "    if missing:", "    if False:",
     "test_cuts.py::test_an_unclassified_counter_account_refuses"),
    ("contracts.py", "        if unknown:\n            raise ContractError", "        if False:\n            raise ContractError",
     "test_contracts.py::test_an_unknown_term_refuses"),
    ("packs/contracts.py", "\"fee\": exact(terms[\"split\"]) * savings", "\"fee\": (exact(terms[\"split\"]) + exact(\"0.0001\")) * savings",
     "test_case_eria.py::test_irr"),
    ("cases/__init__.py", "    top.finish()", "    pass",
     "test_case_eria.py::test_the_case_file_has_no_unread_key"),
]


@pytest.mark.parametrize("fname,old,new,guard", MUTATIONS, ids=[m[3].split("::")[1] for m in MUTATIONS])
def test_switching_off_a_check_turns_its_test_red(tmp_path, fname, old, new, guard):
    copy = tmp_path / "one_engine"
    shutil.copytree(PKG, copy, ignore=shutil.ignore_patterns("__pycache__", "test_mutations.py"))
    target = copy / fname
    src = target.read_text()
    assert src.count(old) == 1, f"mutation anchor not found exactly once in {fname}: {old!r}"
    target.write_text(src.replace(old, new))
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                          f"one_engine/tests/{guard}"], cwd=tmp_path, capture_output=True, text=True)
    assert res.returncode != 0, f"switching off the check in {fname} left {guard} green:\n{res.stdout[-800:]}"
