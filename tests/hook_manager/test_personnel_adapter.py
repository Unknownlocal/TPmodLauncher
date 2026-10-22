# SPDX-License-Identifier: MPL-2.0
"""Driver integration lifecycle and caller cancellation on portable owned code."""

import inspect
import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallableTarget
from hook_manager.game_adapters.regulations import personnel_adapter as implementation


def owned(monkeypatch):
    module = ModuleType("owned_personnel_adapter")
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

    class StaffManager:
        def sign_staff(self, staff, salary=2):
            self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
            self.state.writes.append("staff")
            if self.state.native_refuse:
                return False
            staff.contract = {"salary": salary}
            return True

    class Negotiations:
        def _execute_offer(self, row, candidate):
            if row.get("native_precheck_failure"):
                return False
            if row["candidate_type"] == "staff":
                self.state.prestige_manager.displayed_prestige(self.state.teams["target"])
                if row.get("context", {}).get("entry_id"):
                    candidate._oval_target_entry_id = row["context"]["entry_id"]
                return self.state.staff_manager.sign_staff(candidate)
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

        def resolve_due_decisions(self):
            for rows, candidate in self.markets:
                for row in rows:
                    if self._execute_offer(row, candidate):
                        break
                self.state.writes.append("closed")

        def sign_uncontested(self, candidate_type, candidate, team_name, *, signer_kind="team"):
            ok = self._execute_offer(self.row, candidate)
            return ok, "Native driver will not renew"

        def ai_manage_drivers(self, role_phase="all"):
            ok, reason = self.sign_uncontested("driver", self.driver, "target")
            if not ok:
                self._fail_ai_driver_record(self.row, reason, permanent="will not renew" in reason.lower())

        def _fail_ai_driver_record(self, row, reason, *, permanent=False):
            row.update(failure_reason=reason, permanent_block=permanent,
                       blocked_role="reserve" if not permanent and "reserve role" in reason.lower() else None)
            self.state.writes.append("cooldown")

    module.DriverManager, module.Negotiations, module.PrestigeManager = DriverManager, Negotiations, PrestigeManager
    module.StaffManager = StaffManager
    state = SimpleNamespace(writes=[], teams={"target": {}}, prestige_manager=PrestigeManager(), native_refuse=False)
    manager, negotiations = DriverManager(), Negotiations()
    manager.state = negotiations.state = state
    state.driver_manager, state.personnel_negotiation_manager = manager, negotiations
    state.staff_manager = StaffManager()
    state.staff_manager.state = state
    driver = SimpleNamespace(contract=None)
    negotiations.row = {"candidate_type": "driver", "initiated_by": "player", "offer": {"years": 3}}
    negotiations.driver, negotiations.pending = driver, {"decision": {"status": "pending"}}

    def target(name, owner, method):
        fn = getattr(owner, method)
        return CallableTarget(name, module.__name__, (owner.__name__,), method,
                              str(inspect.signature(fn)))

    signing = target("drivers.sign", DriverManager, "sign_driver")
    callers = (target("personnel.caller.offer", Negotiations, "_execute_offer"),
               target("personnel.caller.decision", Negotiations, "resolve_player_contract_decision"),
               target("personnel.caller.due", Negotiations, "resolve_due_decisions"),
               target("personnel.caller.uncontested", Negotiations, "sign_uncontested"),
               target("personnel.caller.ai_drivers", Negotiations, "ai_manage_drivers"),
               target("personnel.caller.ai_failure", Negotiations, "_fail_ai_driver_record"),
               target("personnel.caller.personalities", DriverManager, "driver_personalities"),
               target("personnel.caller.career", DriverManager, "career_stage_modifiers"),
               target("personnel.caller.prestige", PrestigeManager, "displayed_prestige"))
    monkeypatch.setattr(implementation, "driver_target", lambda: signing)
    monkeypatch.setattr(implementation, "staff_target", lambda: target("staff.sign", StaffManager, "sign_staff"))
    monkeypatch.setattr(implementation, "personnel_caller_targets", lambda kinds: callers)
    monkeypatch.setattr(implementation, "check_preparation", lambda state, kinds: None)

    class Channel:
        mode = "deny"
        message = "Owned refusal"

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
                                    else {"decision": "deny", "message": self.message}]}

    planner = SimpleNamespace(state=state, manager=manager,
                              prepare=lambda arguments: {"clock": {"season": 3, "absolute_week": 114},
                                                         "effective": {"season": 3}, "proposal": {}})
    return state, driver, negotiations, planner, Channel(), signing, callers


def test_market_keeps_native_fallback_and_stops_unavailable_after_prior_completion(monkeypatch):
    state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
    first = {**negotiations.row, "initiated_by": "ai", "offer": {"years": 3}}
    second = {**first, "offer": {"years": 3}}
    negotiations.markets = [([first, second], driver), ([dict(first)], SimpleNamespace(contract=None))]
    original_dispatch = type(channel).__call__
    modes = iter(("deny", "allow", "failed"))
    attempts = []

    def dispatch(*args):
        channel.mode = next(modes)
        attempts.append(channel.mode)
        return original_dispatch(*args)

    monkeypatch.setattr(type(channel), "__call__", dispatch)
    adapter = implementation.PersonnelAdapter({"driver": planner}, channel, lambda *a: {"selected": "current"})
    try:
        assert negotiations.resolve_due_decisions() is None
        assert attempts == ["deny", "allow", "failed"]
        assert state.writes == [2, "closed"] and driver.contract == {"salary": 2}
        assert adapter.last_outcome.status == "unavailable" and not adapter._active
    finally:
        adapter.stop()


def test_ai_unavailability_unwinds_nested_uncontested_but_policy_and_native_failure_continue(monkeypatch):
    for mode in ("deny", "allow", "failed"):
        state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
        channel.mode, state.native_refuse = mode, True
        channel.message = "Rule says driver will not renew this reserve role contract"
        adapter = implementation.PersonnelAdapter({"driver": planner}, channel, lambda *a: {"selected": "current"})
        try:
            assert negotiations.ai_manage_drivers("reserve") is None
            assert state.writes == ({"deny": ["cooldown"], "allow": [2, "cooldown"], "failed": []}[mode])
            assert adapter.last_outcome.status == {"failed": "unavailable"}.get(mode, mode)
            assert driver.contract is None and not adapter._active
            if mode != "failed":
                assert negotiations.row["permanent_block"] is (mode == "allow")
                assert negotiations.row["blocked_role"] is None
                assert negotiations.row["failure_reason"] == (channel.message if mode == "deny" else "Native driver will not renew")
        finally:
            adapter.stop()


def test_driver_guard_cancels_confirmation_keeps_native_exceptions_and_restores(monkeypatch):
    state, driver, negotiations, planner, channel, signing, _ = owned(monkeypatch)
    original = type(state.driver_manager).sign_driver
    adapter = implementation.PersonnelAdapter({"driver": planner}, channel, lambda *a: {"selected": "current"})
    try:
        # Each refused confirmation must remain pending; a native False would
        # otherwise try a backup and close it. Offer preparation must not stick.
        for mode, reason in (("deny", "Owned refusal"), ("failed", "Validation unavailable")):
            channel.mode = mode
            assert negotiations.resolve_player_contract_decision("decision", True) == (False, reason)
            assert adapter.last_outcome.status == ("deny" if mode == "deny" else "unavailable")
            assert "decision" in negotiations.pending and negotiations.row["offer"]["years"] == 3
            assert not state.writes and vars(driver) == {"contract": None} and state.teams == {"target": {}}
        assert adapter.owners == {"owned.rules": "unavailable"}
        # No guard is reached for this new attempt. An old blocked reason may
        # remain on the native record, but it is not this attempt's outcome.
        negotiations.row["native_precheck_failure"] = True
        assert negotiations._execute_offer(negotiations.row, driver) is False
        assert adapter.last_outcome is None and adapter.last_refusal is None
        assert negotiations.row["execution_block_reason"] == "Validation unavailable"
        negotiations.row.pop("native_precheck_failure")
    finally:
        adapter.stop()
    assert type(state.driver_manager).sign_driver is original

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.PersonnelAdapter({"driver": planner}, channel, lambda *a: {"selected": "current"})
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
        assert adapter.last_outcome.status == "allow"
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
    assert implementation.PersonnelAdapter.create(state, channel, None, reports.append) is None and not reports
    monkeypatch.setattr(channel, "bindings", lambda scope: {"owned.rules": ((0, "rule"),)})
    monkeypatch.setattr(implementation, "DriverContractPlanner", lambda *a: planner)
    monkeypatch.setattr(implementation, "StaffContractPlanner", lambda *a: planner)
    monkeypatch.setattr(implementation, "staff_target", lambda: signing)
    originals = type(state.driver_manager).sign_driver, type(negotiations)._execute_offer
    broken = callers[-1]
    broken.signature = "(changed)"
    assert implementation.PersonnelAdapter.create(state, channel, None, reports.append) is None
    assert len(reports) == 2 and "COMPATIBILITY FAILED" in reports[0] and "continuing native game" in reports[0]
    assert (type(state.driver_manager).sign_driver, type(negotiations)._execute_offer) == originals


def test_staff_and_driver_share_callers_without_publishing_refused_staff_preparation(monkeypatch):
    # Reuse the driver lifecycle fixture; only the second signing target and
    # staff-specific pre-sign field write introduce new integration behavior.
    state, driver, negotiations, planner, channel, _, _ = owned(monkeypatch)
    staff = SimpleNamespace(contract=None)
    staff_planner = SimpleNamespace(state=state, manager=state.staff_manager,
                                   prepare=lambda arguments: {"clock": {"season": 3, "absolute_week": 114},
                                                              "effective": {"season": 3}, "proposal": {"kind": "staff"}})
    negotiations.driver = staff
    negotiations.row = {"candidate_type": "staff", "initiated_by": "player", "offer": {"years": 3},
                        "context": {"entry_id": "native-hint"}}
    original_offer = type(negotiations)._execute_offer
    original_staff = type(state.staff_manager).sign_staff
    adapter = implementation.PersonnelAdapter({"driver": planner, "staff": staff_planner}, channel,
                                              lambda *a: {"selected": "current"})
    try:
        assert len([name for name in adapter.groups if name.endswith("caller.offer")]) == 1
        for mode, reason in (("deny", "Owned refusal"), ("failed", "Validation unavailable")):
            channel.mode = mode
            assert negotiations.resolve_player_contract_decision("decision", True) == (False, reason)
            assert "decision" in negotiations.pending and vars(staff) == {"contract": None}
            assert not state.writes and state.teams == {"target": {}}
    finally:
        adapter.stop()
    assert type(negotiations)._execute_offer is original_offer and type(state.staff_manager).sign_staff is original_staff

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.PersonnelAdapter({"driver": planner, "staff": staff_planner}, channel,
                                              lambda *a: {"selected": "current"})
    try:
        assert negotiations.resolve_player_contract_decision("decision", True) == (True, "closed")
        assert staff.contract == {"salary": 2} and staff._oval_target_entry_id == "native-hint"
        assert driver.contract is None and state.writes == ["staff"] and state.teams["target"]["prestige"] == 50
        channel.mode = "deny"
        other = type(state.staff_manager)()
        other.state = SimpleNamespace(writes=[], teams={"target": {}}, prestige_manager=type(state.prestige_manager)(), native_refuse=False)
        assert other.sign_staff(SimpleNamespace(contract=None)) is True and other.state.writes == ["staff"]
    finally:
        adapter.stop()
