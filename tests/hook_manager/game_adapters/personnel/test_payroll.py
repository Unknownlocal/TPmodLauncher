# SPDX-License-Identifier: MPL-2.0
"""Payroll uses prepared contract changes without modifying either roster."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.personnel.payroll import project_payroll
from hook_manager.manager import HookError


def contract(team="A", salary=2, start=1, length=156, **extra):
    return {"team": team, "salary_m": salary, "start_week": start, "length_weeks": length, **extra}


def person(current=None, future=None, **extra):
    return SimpleNamespace(contract=current, future_contract=future, **extra)


def test_immediate_replacement_observes_today_and_preserves_source_records():
    candidate = person(contract(salary=4), contract(salary=8, start=53))
    staff = [candidate, person(contract(salary=3, start=20)),
             person({"team": "A", "salary_m": 1.25, "length_weeks": 52})]
    loan = person(contract(salary=6, academy_loan=True, loan_owner="B"),
                  academy_contract={"annual_cost_m": 500}, team="B")
    # Foreign records do not need their money/date fields to prepare this team.
    drivers = [loan, person(contract(salary=10, bonus_per_win_m=100)),
               person(contract("B", salary=float("inf"), start=False))]
    proposed = contract(salary=5)
    before = deepcopy((drivers, staff, proposed))
    result = project_payroll(drivers, staff, "A", candidate, "staff", proposed, 27, 53)
    assert result == {"as_of_week": 27, "staff_annual_salary_m": 9.25, "driver_annual_salary_m": 16.0}
    assert before == (drivers, staff, proposed)


def test_future_payroll_uses_due_deals_and_exclusive_contract_ends():
    candidate = person(contract(salary=50), contract("B", salary=70, start=53))
    drivers = [candidate, person(contract(salary=100, length=52)),
               person(contract(salary=7), contract("B", salary=10, start=53)),
               person(contract(salary=2), contract("B", salary=80, start=105))]
    staff = [person(contract(salary=5), contract(salary=11, start=53))]
    proposed = contract(salary=9, start=53)
    before = deepcopy((drivers, staff, proposed))
    assert project_payroll(drivers, staff, "A", candidate, "driver", proposed, 27, 53) == {
        "as_of_week": 53, "staff_annual_salary_m": 11.0, "driver_annual_salary_m": 11.0}
    assert before == (drivers, staff, proposed)


def test_generated_candidate_includes_prior_private_hires_without_changing_live_roster():
    live_staff = [person(contract(salary=5))]
    private_staff = [*live_staff, person(contract(salary=2, start=27))]
    proposed = contract(salary=3, start=27)
    before = deepcopy((live_staff, private_staff, proposed))
    assert project_payroll([], private_staff, "A", person(), "staff", proposed, 27, 53) == {
        "as_of_week": 27, "staff_annual_salary_m": 10.0, "driver_annual_salary_m": 0.0}
    assert before == (live_staff, private_staff, proposed)


@pytest.mark.parametrize("change", [{"salary_m": float("nan")}, {"start_week": True}, {"team": "B"}])
def test_invalid_required_terms_cannot_produce_a_zero_cost_agreement(change):
    with pytest.raises(HookError):
        project_payroll([], [], "A", person(), "staff", {**contract(), **change}, 27, 53)


def test_overflowing_salary_total_is_reported_as_unavailable_data():
    with pytest.raises(HookError, match="exceeds finite"):
        project_payroll([], [person(contract(salary=1e308))], "A", person(), "staff",
                        contract(salary=1e308), 27, 53)
