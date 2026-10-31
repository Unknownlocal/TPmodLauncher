# SPDX-License-Identifier: MPL-2.0
"""Requested payroll follows private hiring and refuses stale or missing data."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from tests.hook_manager.negotiation_support import submitted_negotiation as submitted
from tests.hook_manager.staffing_support import staffing_coordinator as coordinator


@pytest.mark.parametrize("scenario", ["allow", "cap", "stale_publication"])
def test_payroll_uses_running_private_hires_and_checks_drivers_before_publication(monkeypatch, scenario):
    with coordinator(monkeypatch) as (state, manager, person, channel, guards, run):
        state.teams = {"first": state.teams["first"]}
        driver = SimpleNamespace(contract={"team": "first", "salary_m": 1, "start_week": 105, "length_weeks": 104},
                                 future_contract=None)
        state.driver_manager = SimpleNamespace(drivers=[driver])
        guards.input_requests = {"owned.rules": frozenset({"employer_payroll"})}

        def onboard(self, team_name, *, contract_years=1):
            signed = []
            for role in ("chief_designer", "technical_director"):
                if not self.team_has_active_staff(team_name, role, week=114):
                    candidate = next((member for member in self.staff if not member.contract), None)
                    candidate = candidate or self._create_free_staff_for_role(role, None)
                    if self.sign_staff(team_name, candidate, 2., contract_years, signer_kind="team",
                                       bypass_requirements=True, allow_current_cover=True):
                        signed.append(candidate.name)
            return signed

        def sign(self, team_name, staff, salary_m, years, *, bonuses=None, signer_kind="team",
                 bypass_requirements=False, allow_current_cover=False, contract_start_week=None):
            staff.contract = {"team": team_name, "salary_m": salary_m, "start_week": 114, "length_weeks": 43}
            return True

        run.planner.methods["staff"]["onboard_joining_team_staff"] = onboard, False
        run.planner.sign = sign
        dispatch = type(channel).__call__
        def check_cap(self, contexts, selections, calls, deadline):
            self.mode = "deny" if scenario == "cap" and contexts[0]["proposal"]["employer"]["payroll"]["staff_annual_salary_m"] > 3 else "allow"
            return dispatch(self, contexts, selections, calls, deadline)
        monkeypatch.setattr(type(channel), "__call__", check_cap)
        if scenario == "stale_publication":
            prepare = run.planner.prepare_onboarding
            def changed(*args, **kwargs):
                plan = prepare(*args, **kwargs)
                driver.contract["salary_m"] = 7
                return plan
            monkeypatch.setattr(run.planner, "prepare_onboarding", changed)
        result = run.onboard("first")
        assert [row["proposal"]["employer"]["payroll"]["staff_annual_salary_m"] for row in channel.actions] == [2., 4.]
        assert result.published is (scenario == "allow")
        if result.published:
            assert len(manager.staff) == 2 and sum(member.contract["salary_m"] for member in manager.staff) == 4
        else:
            assert manager.staff == [person] and person.contract is None
            assert result.refusal.status == ("deny" if scenario == "cap" else "unavailable")
        assert driver.contract["salary_m"] == (7 if scenario == "stale_publication" else 1)
        assert set(guards.owners.values()) == {"ready"}


def test_requested_payroll_previews_and_signing_refuse_stale_or_missing_data(monkeypatch):
    from hook_manager.game_adapters.personnel.payroll import prepare_payroll

    with submitted(monkeypatch) as (state, person, negotiations, channel, adapter):
        state.week_index = 9
        state.driver_manager.drivers = [person]
        staff = SimpleNamespace(contract={"team": "target", "salary_m": 3, "start_week": 105, "length_weeks": 156}, future_contract=None)
        state.staff_manager.staff = [staff]
        adapter.input_requests = {"owned.rules": frozenset({"employer_payroll"})}
        def prepare(arguments, *, payroll_checks=None):
            proposed = {"team": "target", "salary_m": arguments.get("salary_m", arguments.get("salary", 2)),
                        "start_week": 157, "length_weeks": 52}
            employer = {"name": "target", "kind": "team"}
            if payroll_checks is not None:
                employer["payroll"] = prepare_payroll(state, "target", person, "driver", proposed, 114, 157, payroll_checks)
            return {"clock": {"season": 3, "absolute_week": 114},
                    "effective": {"season": 4, "start_week": 157, "end_week": 209}, "proposal": {"employer": employer}}
        adapter.planners["driver"]._build_proposal = prepare
        dispatch = type(channel).__call__
        channel.mode = "allow"
        assert negotiations.submit_offer("driver", person, "target", {"salary_m": 2.5, "years": 1}, 100, finalize=True)[0]
        assert channel.contexts[-1]["proposal"]["employer"]["payroll"] == {
            "as_of_week": 157, "staff_annual_salary_m": 3., "driver_annual_salary_m": 2.5}
        before = deepcopy((negotiations.row, negotiations.candidate_deadlines, state.writes))
        def changed(*args):
            result = dispatch(*args)
            staff.contract["salary_m"] = 4
            return result
        monkeypatch.setattr(type(channel), "__call__", changed)
        assert not negotiations.submit_offer("driver", person, "target", {"salary_m": 3, "years": 1}, 100, finalize=True)[0]
        assert adapter.last_outcome.status == "unavailable" and adapter.owners == {"owned.rules": "ready"}
        assert before == (negotiations.row, negotiations.candidate_deadlines, state.writes)
        monkeypatch.setattr(type(channel), "__call__", dispatch)
        channel.mode = "deny"
        assert not state.driver_manager.sign_driver(person)
        assert channel.contexts[-1]["operation"] == "commit"
        assert channel.contexts[-1]["proposal"]["employer"]["payroll"]["staff_annual_salary_m"] == 4
        state.staff_manager.staff = [object()]
        assert not state.driver_manager.sign_driver(person) and adapter.last_outcome.status == "unavailable"
        assert person.contract is None and before[2] == state.writes
