# SPDX-License-Identifier: MPL-2.0
"""Required staffing stays private until validation, completion and freshness agree."""

from copy import deepcopy
import inspect
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallBinding, CallableTarget, ResultContract
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.personnel_adapter import PersonnelAdapter
from hook_manager.game_adapters.regulations.staffing_coordinator import StaffingCoordinator
from hook_manager.game_adapters.staff.staffing import StaffingPlanner
from hook_manager.manager import HookError
from tests.hook_manager.test_staffing_planner import owned


def coordinator(monkeypatch, mode="allow"):
    state, manager, person = owned(monkeypatch)
    planner = StaffingPlanner(state)

    class Channel:
        def __init__(self):
            self.epoch, self.closed, self.mode = 1, False, mode
            self._lock = threading.Lock()
            self._generations = dict.fromkeys(("owned.rules", "peer.rules"), 1)
            self._owners = {owner: {"revision": "owned"} for owner in self._generations}
            self._unavailable, self.actions, self.calls = set(), [], []
            self.after = lambda: None

        def bindings(self, scope):
            return {owner: ((0, "rule"),) for owner in self._owners}

        def __call__(self, contexts, selections, calls, deadline):
            assert contexts[0]["initiator"] == "system" and contexts[0]["operation"] == "commit"
            self.actions.append(contexts[0])
            self.calls.append(calls)
            if self.mode == "unavailable":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned unavailable handler")
            result = {owner: [{"decision": "allow"} if self.mode == "allow" else
                              {"decision": "deny", "message": "Owned policy refusal"}] for owner in calls}
            self.after()
            return result

    class Guards(PersonnelAdapter):
        def __init__(self, channel):
            RegulationGuards.__init__(self, channel, lambda *args: {"selected": "current"}, "personnel.contract")
            self.planners, self.feedback = {"staff": planner.contracts}, None

    channel = Channel()
    guards = Guards(channel)
    module = ModuleType("owned_staffing_coordinator")
    module.StaffManager = type(manager)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    guards.approve(CallableTarget("staff.sign", module.__name__, ("StaffManager",), "sign_staff",
                                  str(inspect.signature(planner.sign))))
    codec = ResultContract({"shape": "bool", "denied": {"literal": False}})
    project = lambda arguments, result: {}
    for owner in channel._owners:
        guards.register(owner, [CallBinding("staff", "staff.sign", project, codec)])
    return state, manager, person, channel, guards, StaffingCoordinator(guards, planner)


def test_complete_staffing_publishes_native_effects_and_preserves_canonical_owners(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    roster, history, effects = manager.staff, manager.staff_season_history, person.morale_effects
    old_history = history[person.name]
    try:
        result = run.recover()
        assert result.published and result.native_result is None and not result.missing and result.refusal is None
        assert manager.staff is roster and manager.staff_season_history is history
        assert roster[0] is person and person.contract == {"team": "first"}
        assert person.morale_effects is effects and effects == [{"amount": 1}]
        assert history[person.name] is old_history and history["Generated"] == []
        generated = roster[1]
        assert generated.contract == {"team": "second"}
        assert channel.actions[1]["proposal"]["person"]["ref"] == "staff:" + str(id(generated))
        assert not guards._active and guards._local.staffing_caller is None
    finally:
        guards.stop()


def test_denied_unavailable_and_native_incomplete_staffing_discard_all_private_work(monkeypatch):
    for mode in ("deny", "unavailable", "late_deny", "native_false"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch, mode if mode in ("deny", "unavailable") else "allow")
        before = deepcopy((vars(person), manager.staff_season_history))
        if mode == "late_deny":
            channel.after = lambda: setattr(channel, "mode", "deny")
        manager.refuse = mode == "native_false"
        try:
            result = run.recover()
            assert not result.published and manager.staff == [person]
            assert (vars(person), manager.staff_season_history) == before
            if mode == "native_false":
                assert result.refusal is None and guards.last_outcome.status == "allow"
            else:
                assert result.refusal.status == ("unavailable" if mode == "unavailable" else "deny")
            assert bool(result.missing) is (mode != "unavailable")
            assert len(channel.actions) == (1 if mode == "unavailable" else 2)
            assert not guards._active and guards._local.staffing_caller is None
        finally:
            guards.stop()


def test_stale_inputs_and_policy_block_publication_without_overwriting_external_changes(monkeypatch):
    class Contract(dict):
        pass

    for change in ("roster", "clock", "policy", "contract"):
        state, manager, person, channel, guards, run = coordinator(monkeypatch)
        extra = SimpleNamespace(name="External", role="chief_designer", retired=True, contract=None)
        if change == "contract":
            person.contract = Contract()
        def mutate():
            if change == "roster" and all(member is not extra for member in manager.staff):
                manager.staff.append(extra)
            elif change == "clock":
                state.week_index += 1
            elif change == "policy":
                channel._generations["owned.rules"] += 1
            elif change == "contract":
                person.contract["team"] = "external"
        channel.after = mutate
        try:
            result = run.recover()
            assert not result.published and result.refusal.status == "unavailable"
            assert person.contract == ({"team": "external"} if change == "contract" else None)
            assert not person.morale_effects
            assert manager.staff == ([person, extra] if change == "roster" else [person])
            assert "Generated" not in manager.staff_season_history and not guards._active
        finally:
            guards.stop()


def test_removed_owners_do_not_validate_or_block_later_staffing(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    try:
        guards.remove_owner("owned.rules")
        assert run.onboard("first").published
        assert set(channel.calls[-1]) == {"peer.rules"}
        guards.remove_owner("peer.rules")
        actions = len(channel.actions)
        channel.closed = True
        channel._lock.acquire()
        try:
            assert run.onboard("second").published
        finally:
            channel._lock.release()
        assert len(channel.actions) == actions and manager.staff[-1].contract == {"team": "second"}
    finally:
        guards.stop()


def test_generation_error_and_unadmitted_worker_leave_staffing_private(monkeypatch):
    state, manager, person, channel, guards, run = coordinator(monkeypatch)
    create = run.planner.methods["staff"]["_create_free_staff_for_role"][0]
    def failed(self, role, rng):
        create(self, role, rng)
        raise ValueError("Owned generation error")
    run.planner.methods["staff"]["_create_free_staff_for_role"] = failed, False
    try:
        with pytest.raises(ValueError, match="Owned generation error"):
            run.recover()
        assert manager.staff == [person] and person.contract is None and not guards._active
        errors = []
        def worker():
            try:
                run.recover()
            except HookError as error:
                errors.append(str(error))
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and len(errors) == 1
        assert manager.staff == [person] and person.contract is None and not guards._active
    finally:
        guards.stop()
