# SPDX-License-Identifier: MPL-2.0
"""Driver integration lifecycle and caller cancellation on portable owned code."""

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallableTarget, code_digest
from hook_manager.game_adapters.regulations import driver_adapter as implementation


def owned(monkeypatch):
    module = ModuleType("owned_driver_adapter")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class DriverManager:
        def sign_driver(self, driver, salary=2):
            self.state.writes.append(salary)
            if salary == 0:
                raise ValueError("Native failure")
            if self.state.native_refuse:
                return False
            driver.contract = {"salary": salary}
            return True

        def driver_personalities(self, driver):
            driver.personality = "loyal"
            driver.personalities = ["loyal"]
            return driver.personalities

        def career_stage_modifiers(self, driver):
            driver.career_stage = "youngster"
            return {"key": driver.career_stage}

    # Native getters write these fields too. The local native probe exercises
    # their real bodies; these small stand-ins keep the same isolation boundary.
    class PrestigeManager:
        def displayed_prestige(self, team):
            team["prestige"] = 50
            return 50

    class Negotiations:
        def _execute_offer(self, row, candidate):
            self.state.driver_manager.driver_personalities(candidate)
            self.state.driver_manager.career_stage_modifiers(candidate)
            self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
            row["offer"]["years"] = 1
            signed = self.state.driver_manager.sign_driver(candidate)
            if not signed:
                row["execution_block_reason"] = "Native final validation failed"
            return signed

        def resolve_player_contract_decision(self, decision_id, accept):
            signed = self._execute_offer(self.row, self.driver)
            if not signed:
                self.state.writes.append("backup")
            self.pending.pop(decision_id)
            return True, "closed"

    module.DriverManager, module.Negotiations, module.PrestigeManager = DriverManager, Negotiations, PrestigeManager
    state = SimpleNamespace(writes=[], teams={"target": {}}, prestige_manager=PrestigeManager(), native_refuse=False)
    manager, negotiations = DriverManager(), Negotiations()
    manager.state = negotiations.state = state
    state.driver_manager, state.personnel_negotiation_manager = manager, negotiations
    driver = SimpleNamespace(contract=None)
    negotiations.row = {"candidate_type": "driver", "initiated_by": "player", "offer": {"years": 3}}
    negotiations.driver, negotiations.pending = driver, {"decision": {"status": "pending"}}

    def target(name, owner, method):
        fn = getattr(owner, method)
        return CallableTarget(name, module.__name__, (owner.__name__,), method,
                              str(inspect.signature(fn)), (code_digest(fn.__code__),))

    signing = target("drivers.sign", DriverManager, "sign_driver")
    callers = (target("drivers.caller.offer", Negotiations, "_execute_offer"),
               target("drivers.caller.decision", Negotiations, "resolve_player_contract_decision"),
               target("drivers.caller.personalities", DriverManager, "driver_personalities"),
               target("drivers.caller.career", DriverManager, "career_stage_modifiers"),
               target("drivers.caller.prestige", PrestigeManager, "displayed_prestige"))
    monkeypatch.setattr(implementation, "driver_target", lambda: signing)
    monkeypatch.setattr(implementation, "driver_caller_targets", lambda: callers)
    monkeypatch.setattr(implementation, "check_preparation", lambda state: None)

    class Channel:
        mode = "deny"

        def __init__(self):
            self._unavailable = set()

        def bindings(self, scope):
            return {"owned.rules": ((0, "rule"),)}

        def __call__(self, contexts, selections, calls, deadline):
            assert contexts[0]["schema"] == 1 and contexts[0]["scope"] == "personnel.contract"
            assert contexts[0]["operation"] == "commit"
            if self.mode == "failed":
                self._unavailable.add("owned.rules")
                raise RuntimeError("Owned handler failed")
            return {"owned.rules": [{"decision": "allow"} if self.mode == "allow"
                                    else {"decision": "deny", "message": "Owned refusal"}]}

    planner = SimpleNamespace(state=state, manager=manager,
                              prepare=lambda arguments: {"clock": {"season": 3, "absolute_week": 114},
                                                         "effective": {"season": 3}, "proposal": {}})
    return state, driver, negotiations, planner, Channel(), signing, callers


def test_driver_guard_cancels_confirmation_keeps_native_exceptions_and_restores(monkeypatch):
    state, driver, negotiations, planner, channel, signing, _ = owned(monkeypatch)
    original = type(state.driver_manager).sign_driver
    adapter = implementation.DriverAdapter(planner, channel, lambda *a: {"selected": "current"})
    try:
        # Each refused confirmation must remain pending; a native False would
        # otherwise try a backup and close it. Offer preparation must not stick.
        for mode, reason in (("deny", "Owned refusal"), ("failed", "Validation unavailable")):
            channel.mode = mode
            assert negotiations.resolve_player_contract_decision("decision", True) == (False, reason)
            assert "decision" in negotiations.pending and negotiations.row["offer"]["years"] == 3
            assert not state.writes and vars(driver) == {"contract": None} and state.teams == {"target": {}}
        assert adapter.owners == {"owned.rules": "unavailable"}
    finally:
        adapter.stop()
    assert type(state.driver_manager).sign_driver is original

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.DriverAdapter(planner, channel, lambda *a: {"selected": "current"})
    try:
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert state.writes == [2] and negotiations.row["offer"]["years"] == 1
        assert driver.personality == "loyal" and driver.career_stage == "youngster" and state.teams["target"]["prestige"] == 50
        with pytest.raises(ValueError, match="Native failure"):
            state.driver_manager.sign_driver(driver, 0)
        assert state.writes == [2, 0] and adapter.owners == {"owned.rules": "ready"}
        state.native_refuse = True
        negotiations.pending["decision"] = {"status": "pending"}
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert state.writes[-2:] == [2, "backup"] and adapter.last_refusal is None
        other = type(state.driver_manager)()
        other.state = SimpleNamespace(writes=[], native_refuse=False)
        channel.mode = "deny"
        assert other.sign_driver(SimpleNamespace(contract=None)) is True and other.state.writes == [2]
    finally:
        adapter.stop()
    assert type(state.driver_manager).sign_driver is original


def test_driver_creation_skips_unsubscribed_or_incompatible_callers_atomically(monkeypatch):
    state, driver, negotiations, planner, channel, signing, callers = owned(monkeypatch)
    reports = []
    monkeypatch.setattr(channel, "bindings", lambda scope: {})
    monkeypatch.setattr(implementation, "DriverContractPlanner", lambda *a: pytest.fail("No subscriptions must skip inspection"))
    assert implementation.DriverAdapter.create(state, channel, None, reports.append) is None and not reports
    monkeypatch.setattr(channel, "bindings", lambda scope: {"owned.rules": ((0, "rule"),)})
    monkeypatch.setattr(implementation, "DriverContractPlanner", lambda *a: planner)
    originals = type(state.driver_manager).sign_driver, type(negotiations)._execute_offer
    broken = callers[-1]
    broken.signature = "(changed)"
    assert implementation.DriverAdapter.create(state, channel, None, reports.append) is None
    assert len(reports) == 1 and "COMPATIBILITY FAILED" in reports[0] and "continuing native game" in reports[0]
    assert (type(state.driver_manager).sign_driver, type(negotiations)._execute_offer) == originals
