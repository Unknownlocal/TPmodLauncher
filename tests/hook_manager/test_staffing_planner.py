# SPDX-License-Identifier: MPL-2.0
"""Private staffing batches retain canonical people and isolate native attempts."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from hook_manager.game_adapters.staff import staffing
from hook_manager.manager import HookError
from tests.hook_manager.staffing_support import staffing_state as owned


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
