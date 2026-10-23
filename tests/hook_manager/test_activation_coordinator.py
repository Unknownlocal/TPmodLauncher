# SPDX-License-Identifier: MPL-2.0
"""All due agreements must pass before any native activation effects begin."""

from copy import deepcopy
import inspect
import threading
from types import SimpleNamespace
from typing import Optional

import pytest

from hook_manager.game_adapters.personnel import activation_methods
from hook_manager.game_adapters.personnel.activation import ActivationPlanner
from hook_manager.game_adapters.regulations.activation_coordinator import ActivationCoordinator
from hook_manager.game_adapters.regulations.guards import RegulationGuards
from hook_manager.game_adapters.regulations.personnel_adapter import PersonnelAdapter
from hook_manager.manager import HookError
from tests.hook_manager.test_contract_activation import owned


def coordinator(monkeypatch, mode="allow"):
    state, driver, other, staff, module, calls = owned(monkeypatch)
    state.effects = []

    class Driver:
        pass

    Driver.__module__, Driver.__qualname__ = "managers.driver", "Driver"

    def set_driver(self, driver: Driver, team_name: Optional[str]):
        state.effects.append(("driver", driver.name, team_name))
        driver.team = team_name
        self._invalidate_ai_signing_query_cache()
        if state.native_error:
            raise ValueError("Owned native activation error")

    def invalidate(self) -> None:
        self.cache.clear()

    def set_staff(self, staff: "Staff", team_name: "Optional[str]"):
        state.effects.append(("staff", staff.name, team_name))
        staff.team = team_name

    monkeypatch.setattr(type(state.driver_manager), "set_team", set_driver)
    monkeypatch.setattr(type(state.driver_manager), "_invalidate_ai_signing_query_cache", invalidate, raising=False)
    monkeypatch.setattr(type(state.staff_manager), "set_team", set_staff)
    # Development Python may render Optional[str] as str | None. The local
    # native probe checks the unmodified production signature against Python 3.13.
    monkeypatch.setitem(activation_methods._ACTIVATION_EXECUTION["driver"], "set_team", str(inspect.signature(set_driver)))
    state.native_error, state.driver_manager.cache = False, {"owned": "cached"}
    planner = ActivationPlanner(state)

    class Channel:
        def __init__(self):
            self.epoch, self.closed = 1, False
            self._lock = threading.Lock()
            self._generations = {"owned.rules": 1, "peer.rules": 1}
            self._owners = {owner: {"revision": "current"} for owner in self._generations}
            self._unavailable, self.actions = set(), []
            self.after = lambda: None

        def bindings(self, scope):
            return {owner: ((0, "rule"),) for owner in self._owners}

        def __call__(self, contexts, selections, calls, deadline):
            # Even the final proposal must be checked before the first live
            # setter runs, and dispatch cannot inherit the channel policy lock.
            assert not self._lock.locked() and not state.effects
            context = contexts[0]
            assert context["operation"] == "activate" and context["initiator"] == "system"
            self.actions.append((context, calls))
            self.after()
            if mode == "unavailable":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned validator failure")
            denied = mode == "deny" or mode == "late_deny" and context["proposal"]["person"]["kind"] == "staff"
            return {owner: [{"decision": "deny", "message": "Owned policy refusal"} if denied else
                            {"decision": "allow"}] for owner in calls}

    class Guards(PersonnelAdapter):
        def __init__(self, channel):
            RegulationGuards.__init__(self, channel, lambda *args: {"selected": "current"}, "personnel.contract")
            self.planners = {kind: SimpleNamespace(state=state, manager=manager) for kind, manager in planner.managers.items()}
            self._personnel_operation, self.feedback = threading.Lock(), None
            self.owners = dict.fromkeys(channel._owners, "ready")
            self.entries = {(owner, kind): SimpleNamespace(target=kind + ".sign")
                            for owner in self.owners for kind in planner.kinds}

    channel = Channel()
    guards = Guards(channel)
    return state, driver, staff, channel, guards, ActivationCoordinator(guards, planner), calls


def test_all_proposals_precede_native_activation_and_canonical_effects(monkeypatch):
    state, driver, staff, channel, guards, run, calls = coordinator(monkeypatch)
    future, roster = driver.future_contract, state.drivers
    assert run.activate(131)
    assert len(channel.actions) == 3 and all(set(calls) == {"owned.rules", "peer.rules"} for _, calls in channel.actions)
    assert calls == ["driver", "staff", "driver", "staff"]
    assert driver.contract is future and driver.contract is state.drivers[1].contract
    assert driver.future_contract is staff.future_contract is None and driver.team == "target" and staff.team == "engine"
    assert state.drivers is roster and len(state.effects) == 3 and not state.driver_manager.cache
    assert guards.last_outcome.status == "allow" and not guards._active and not guards._personnel_operation.locked()


def test_policy_denials_and_unavailable_checks_leave_whole_activation_untouched(monkeypatch):
    for mode in ("deny", "late_deny", "unavailable"):
        state, driver, staff, channel, guards, run, calls = coordinator(monkeypatch, mode)
        before = deepcopy([vars(person) for person in state.drivers + state.staff_manager.staff])
        assert not run.activate(131)
        assert [vars(person) for person in state.drivers + state.staff_manager.staff] == before
        assert not state.effects and state.driver_manager.cache == {"owned": "cached"}
        assert calls == ["driver", "staff"]
        assert len(channel.actions) == (3 if mode == "late_deny" else 1)
        assert guards.last_outcome.status == ("unavailable" if mode == "unavailable" else "deny")
        assert not guards._active and not guards._personnel_operation.locked()


def test_stale_native_inputs_or_policy_block_all_effects_without_latching_healthy_owners(monkeypatch):
    class Contract(dict):
        pass

    for change in ("clock", "roster", "agreement", "owner", "supplier", "person", "policy", "copy"):
        state, driver, staff, channel, guards, run, calls = coordinator(monkeypatch)
        if change == "agreement":
            driver.future_contract = Contract(driver.future_contract)
        extra = SimpleNamespace(name="External", future_contract=None)
        def mutate():
            if change == "clock":
                state.week_index += 1
            elif change == "roster" and all(person is not extra for person in state.drivers):
                state.drivers.append(extra)
            elif change == "agreement":
                driver.future_contract["salary_m"] += 1
            elif change == "owner":
                state.teams["target"] = dict(state.teams["target"])
            elif change == "supplier":
                state.suppliers["engine"] = SimpleNamespace(active=False)
            elif change == "person":
                staff.nationality = "FR"
            elif change == "policy":
                channel._owners["peer.rules"]["revision"] = "changed"
        if change == "copy":
            from hook_manager.game_adapters.personnel import activation
            original_copy = activation.copy
            def copying(value):
                result = original_copy(value)
                if value is driver:
                    driver.future_contract["salary_m"] += 1
                return result
            monkeypatch.setattr(activation, "copy", copying)
        channel.after = mutate
        assert not run.activate(131)
        assert guards.last_outcome.status == "unavailable" and set(guards.owners.values()) == {"ready"}
        assert not state.effects and driver.contract is staff.contract is None
        assert driver.future_contract is not None and staff.future_contract is not None
        assert not guards._active and not guards._personnel_operation.locked()


def test_no_owner_no_due_busy_reentrant_and_native_failure_boundaries(monkeypatch):
    for mode in ("removed", "not_due", "busy", "nested", "native_error"):
        state, driver, staff, channel, guards, run, calls = coordinator(monkeypatch)
        if mode == "removed":
            guards.entries.clear()
            guards.owners.clear()
            channel.closed = True
            channel._lock.acquire()
        elif mode == "not_due":
            for person in state.drivers + state.staff_manager.staff:
                if isinstance(getattr(person, "future_contract", None), dict):
                    person.future_contract["start_week"] = 132
        elif mode == "busy":
            guards._personnel_operation.acquire()
        elif mode == "nested":
            def nested():
                with pytest.raises(HookError, match="during an operation"):
                    run.activate(131)
                assert not guards.try_stop()
            channel.after = nested
        else:
            state.native_error = True
        try:
            if mode == "native_error":
                with pytest.raises(ValueError, match="Owned native activation error"):
                    run.activate(131)
                assert len(state.effects) == 1 and staff.contract is None
                assert calls == ["driver", "staff", "driver"] and guards.last_outcome.status == "allow"
            else:
                assert run.activate(131) is (mode != "busy")
                assert not channel.actions if mode in ("removed", "not_due", "busy") else len(channel.actions) == 3
                if mode in ("not_due", "busy"):
                    assert not state.effects
            assert not guards._active
            assert guards._personnel_operation.locked() is (mode == "busy")
        finally:
            if mode == "removed":
                channel._lock.release()
            if mode == "busy":
                guards._personnel_operation.release()


def test_wrong_owner_signatures_and_workers_cannot_start_activation(monkeypatch):
    state, driver, staff, channel, guards, run, calls = coordinator(monkeypatch)
    other_guards = SimpleNamespace(planners={})
    with pytest.raises(HookError, match="matching native personnel"):
        ActivationCoordinator(other_guards, run.planner)
    monkeypatch.setattr(type(state.staff_manager), "set_team", lambda self: None)
    with pytest.raises(HookError, match="signature"):
        ActivationCoordinator(guards, run.planner)
    errors = []
    def worker():
        try:
            run.activate(131)
        except HookError as error:
            errors.append(str(error))
    for enrolled in (False, True):
        thread = threading.Thread(target=worker)
        if enrolled:
            guards.enroll(thread)
        thread.start()
        thread.join(2)
        assert not thread.is_alive()
    assert len(errors) == 2 and not calls and not channel.actions and not state.effects and not guards._active
