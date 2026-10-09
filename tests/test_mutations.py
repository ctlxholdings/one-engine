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

    # step 6, Averta SPV: allocation, conditions, measurement, tax, group, rulebooks
    ("allocation.py", "        remaining -= x", "        remaining -= 0",
     "test_allocation.py::test_tiers_paid_in_order_and_residual_named"),
    ("allocation.py", "        if tier.blocked_by:", "        if False:",
     "test_allocation.py::test_blocked_tier_pays_nothing_and_says_why"),
    ("conditions.py", "        if test.if_missing is None:", "        if False:",
     "test_conditions.py::test_missing_fact_follows_the_declared_rule"),
    ("measurement.py", "        if self.first_charge not in _FIRST_CHARGE:", "        if False:",
     "test_measurement.py::test_first_charge_is_declared_and_moves_the_schedule"),
    ("measurement.py", "x = min(tr.cost / self.policy.life_periods, tr.cost - tr.charged)",
     "x = tr.cost / self.policy.life_periods",
     "test_measurement.py::test_nbv_floor_and_disposal_at_end"),
    ("tax.py", "    if missing:\n        raise TaxError", "    if False:\n        raise TaxError",
     "test_tax.py::test_an_unclassified_profit_and_loss_line_refuses"),
    ("tax.py", "self.limit is None or period - v[0] < self.limit]", "True]",
     "test_tax.py::test_loss_vintage_expires_on_the_clock"),
    ("entities.py", "        if event in self._legs:", "        if False:",
     "test_entities.py::test_an_event_id_used_twice_refuses"),
    ("entities.py", "        if unpaid > 0 and claim.spec.shortfall is not None:", "        if False:",
     "test_entities.py::test_unpaid_interest_is_capitalised_for_a_shareholder_loan"),
    ("entities.py", "        if not 0 <= paid <= claim.due:", "        if False:",
     "test_entities.py::test_paying_more_than_is_due_refuses"),
    ("packs/pf.py", "    return sorted(t for t, v in net.items() if v != 0)", "    return []",
     "test_case_vanilla.py::test_planted_overfunding_fails_sources_equal_uses"),
    ("packs/pf.py", "            if res.verdict == \"fail\":", "            if False:",
     "test_case_vanilla.py::test_lock_up_traps_cash_pik_the_shl_then_releases"),
    ("packs/contracts.py", "for p in range(t + 1, t + 1 + int(terms[\"periods_forward\"]))",
     "for p in range(t + 1, t + int(terms[\"periods_forward\"]))",
     "test_case_vanilla.py::test_anchors"),
    ("packs/contracts.py", "/ exact(terms[\"day_count_basis\"])", "/ 365",
     "test_case_vanilla.py::test_parity_every_period"),

    # step 7, 50MW feature case
    ("inputs.py", "            if key_node.tag == \"tag:yaml.org,2002:merge\":\n                continue\n", "",
     "test_inputs.py::test_a_merge_keeps_one_source_and_explicit_keys_override_it"),
    ("measurement.py", "        if period == self.settle_at:\n            x = self.amount - self.charged",
     "        if period == self.settle_at:\n            x = Fraction(0)",
     "test_measurement.py::test_provision_accrues_then_trues_up_and_pays_once"),
    ("packs/pf.py", "kept = retain[0] if retain[1] <= t <= retain[2] else Fraction(0)", "kept = Fraction(0)",
     "test_case_feature.py::test_minimum_cash_is_a_stock_not_a_flow"),
    ("packs/pf.py", "{acct: -1, gain: 1}", "{acct: -1, gain: -1}",
     "test_case_feature.py::test_unpaid_bullet_shl_is_forgiven_as_a_gain"),
    ("packs/contracts.py", "        if t <= int(terms[\"available_until\"]):\n            undrawn",
     "        if True:\n            undrawn",
     "test_case_feature.py::test_commitment_fee_stops_when_availability_ends"),
    ("packs/contracts.py", "    return min(t + int(terms[\"vat_refund_lag\"]), int(terms[\"end\"]))",
     "    return t + int(terms[\"vat_refund_lag\"])",
     "test_case_feature.py::test_integrity_wind_down_and_sources_equal_uses"),
    ("packs/contracts.py", "    if t >= int(terms[\"end\"]):\n        return exact(0)\n    return amount", "    return amount",
     "test_case_feature.py::test_integrity_wind_down_and_sources_equal_uses"),
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
