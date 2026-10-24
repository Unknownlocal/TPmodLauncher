# SPDX-License-Identifier: MPL-2.0
"""Private staffing batches retain canonical people and isolate native attempts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.staff import planner as contracts
from hook_manager.game_adapters.staff import staffing
from hook_manager.manager import HookError


def owned(monkeypatch):
    class Manager:
        def _create_free_staff_for_role(self, role, rng):
            person = SimpleNamespace(name="Generated", role=role, nationality="GB", age=35,
                                     contract=None, future_contract=None, morale_effects=[], retired=False)
            self.staff.append(person)
            self.staff_season_history[person.name] = []
            return person

        def onboard_joining_team_staff(self, team_name, *, contract_years=1):
            if self.team_has_active_staff(team_name, "chief_designer", week=114):
                return []
            person = next((person for person in self.staff if not person.retired and not person.contract), None)
            if person is None:
                person = self._create_free_staff_for_role("chief_designer", None)
            ok = self.sign_staff(team_name, person, 2., contract_years, signer_kind="team",
                                 bypass_requirements=True, allow_current_cover=True)
            return [person.name] if ok else []

        def ensure_active_teams_have_staff(self, roles=None):
            for name in self.state.teams:
                self.onboard_joining_team_staff(name)

        def sign_staff(self, team_name, staff, salary_m, years, *, bonuses=None, signer_kind="team",
                       bypass_requirements=False, allow_current_cover=False, contract_start_week=None):
            if self.refuse:
                return False
            staff.contract = {"team": team_name}
            staff.morale_effects.append({"amount": 1})
            return True

        def team_has_active_staff(self, team_name, role, week=None):
            return any(getattr(person, "role", None) == role and (person.contract or {}).get("team") == team_name
                       for person in self.staff)

    manager = Manager()
    manager.refuse = False
    person = SimpleNamespace(name="Existing", role="chief_designer", nationality="GB", age=40,
                             contract=None, future_contract=None, morale_effects=[], retired=False)
    manager.staff = [person]
    manager.staff_season_history = {person.name: [{"season": 2}]}
    manager._name_pool = SimpleNamespace(first_names=["Owned"])
    state = SimpleNamespace(game_mode="formula", season_no=3, week_index=9,
                            teams={"first": {"active": True}, "second": {"active": True}}, staff_manager=manager)
    manager.state = state
    state.current_season_start_week = lambda: 105
    state.next_season_start = lambda: 157
    queries = {
        "state": {"current_season_start_week": (lambda self: 105, False), "next_season_start": (lambda self: 157, False)},
        "staff": {"normalize_role": (lambda role: role, True),
                  "team_has_active_staff": (lambda self, *args, **kwargs: False, False)},
    }
    monkeypatch.setattr(contracts, "staff_methods", lambda state: queries)
    methods = {name: (getattr(Manager, name), False) for name in (
        "_create_free_staff_for_role", "onboard_joining_team_staff", "ensure_active_teams_have_staff", "team_has_active_staff")}
    monkeypatch.setattr(staffing, "staffing_methods", lambda manager: {"staff": methods, "names": {}})
    return state, manager, person


def test_staffing_batch_keeps_canonical_refs_and_uncommitted_generation(monkeypatch):
    state, manager, person = owned(monkeypatch)
    # A retired extension record must be read, not recursively copied with the
    # whole roster. Only selected people need a private signing copy.
    def cannot_copy(memo):
        raise AssertionError("Unselected staff was copied")

    ignored = SimpleNamespace(retired=True, contract=None, __deepcopy__=cannot_copy)
    manager.staff.append(ignored)
    roster, history, effects = manager.staff, manager.staff_season_history, person.morale_effects
    before = deepcopy((vars(person), history, state.teams))
    plan = staffing.StaffingPlanner(state).prepare_recovery()
    assert plan.native_result is None and len(plan.signings) == 2
    first, second = plan.signings
    assert first.arguments["staff"] is person and first.arguments["self"] is manager
    assert first.prepared["proposal"]["person"]["ref"] == "staff:" + str(id(person))
    assert first.prepared["proposal"]["employer"]["ref"] == "team:" + str(id(state.teams["first"]))
    assert first.prepared["effective"] == {"season": 3, "start_week": 114, "end_week": 157}
    assert second.arguments["staff"] is plan.generated[0]
    assert second.prepared["proposal"]["person"]["ref"] == "staff:" + str(id(plan.generated[0]))
    assert all(sign.native_accepted for sign in plan.signings)
    assert not plan.generated[0].contract and plan.history_entries == {"Generated": []}
    assert manager.staff is roster and manager.staff == [person, ignored]
    assert manager.staff_season_history is history and person.morale_effects is effects
    assert before == (vars(person), history, state.teams)


def test_staffing_native_refusal_and_generation_error_do_not_publish(monkeypatch):
    state, manager, person = owned(monkeypatch)
    planner = staffing.StaffingPlanner(state)
    manager.refuse = True
    plan = planner.prepare_onboarding("first")
    assert plan.native_result == [] and not plan.signings[0].native_accepted
    assert plan.signings[0].arguments["staff"] is person and person.contract is None
    manager.staff.clear()
    methods = planner.methods["staff"]
    create = methods["_create_free_staff_for_role"][0]

    def failed(self, role, rng):
        create(self, role, rng)
        raise ValueError("Owned generation failure")

    methods["_create_free_staff_for_role"] = failed, False
    history = deepcopy(manager.staff_season_history)
    with pytest.raises(ValueError, match="Owned generation failure"):
        planner.prepare_onboarding("first")
    assert manager.staff == [] and manager.staff_season_history == history
    state.staff_manager = SimpleNamespace()
    with pytest.raises(HookError, match="another native state"):
        planner.prepare_recovery()
