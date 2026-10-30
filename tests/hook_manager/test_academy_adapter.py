# SPDX-License-Identifier: MPL-2.0
"""Academy guard ownership, AI drafts and refusal feedback on portable code."""

from copy import deepcopy
import inspect
import random
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.callables import CallableTarget
from hook_manager.game_adapters.regulations import regulation_academy as implementation


def owned(monkeypatch):
    module = ModuleType("owned_academy_adapter")
    monkeypatch.setitem(sys.modules, module.__name__, module)

    class DriverManager:
        def active_contract(self, person, week=None):
            if person.contract:
                person.contract["normalized"] = True
            if self._ai_signing_query_cache is not None:
                self._ai_signing_query_cache["queried"] = True
            return None

    class AcademyManager:
        def ensure_team_state(self, team):
            academy = team.setdefault("academy", {})
            academy.setdefault("drivers", [])
            academy.setdefault("contracts", {})
            academy.setdefault("placements", {})
            return academy

        def sign_driver(self, team_name, driver_name, seasons=1):
            academy = self.ensure_team_state(self.state.teams[team_name])
            self.state.calls.append("sign")
            if self.state.native_error:
                raise ValueError("Owned native error")
            if self.state.native_refuse:
                return False, "Native refusal"
            contract = {"term": seasons}
            academy["contracts"][driver_name] = self.state.drivers[0].academy_contract = contract
            academy["drivers"].append(driver_name)
            self.state.teams[team_name]["balance_m"] -= 1
            return True, "Signed"

        def renew_contract(self, team_name, driver_name, extra_seasons):
            self.state.calls.append("renew")
            self.state.teams[team_name]["academy"]["contracts"][driver_name]["term"] += extra_seasons
            return True, "Renewed"

        def ai_sign_drivers(self, season):
            self.state.driver_manager.active_contract(self.state.drivers[0])
            self._rng.choice([1, 2])
            academy = self.ensure_team_state(self.state.teams["target"])
            academy["ai_last_sign_season"] = season
            self.sign_driver("target", "Junior", 2)

        def ai_renew_contracts(self, current_season):
            self._rng.choice([1, 2])
            self.ensure_team_state(self.state.teams["target"])
            self.renew_contract("target", "Junior", 1)

    class Screen:
        def handle_event(self, event):
            if event:
                ok, reason = self.state.academy_manager.sign_driver("target", "Junior", 2)
                if ok:
                    self.selected = "Junior"

        def draw(self, surface):
            self.draws += 1

    module.AcademyManager, module.Screen = AcademyManager, Screen
    driver = SimpleNamespace(contract={"team": "old"}, academy_contract=None)
    state = SimpleNamespace(teams={"target": {"name": "target", "balance_m": 10.}}, drivers=[driver],
                            calls=[], native_error=False, native_refuse=False, _rng=random.Random(1234))
    manager = AcademyManager()
    manager.state = state
    state.academy_manager = manager
    state.driver_manager = DriverManager()
    state.driver_manager.state = state
    state.driver_manager._ai_signing_query_cache = {"live": "untouched"}
    screen = Screen()
    screen.state, screen.selected, screen.draws = state, None, 0

    def target(name, owner, method):
        return CallableTarget(name, module.__name__, (owner.__name__,), method, str(inspect.signature(getattr(owner, method))))

    starts = tuple(target("academy." + role, AcademyManager, name)
                   for role, name in (("sign", "sign_driver"), ("renew", "renew_contract")))
    callers = tuple(target("academy.caller." + role, owner, name) for role, owner, name in (
        ("ui", Screen, "handle_event"), ("draw", Screen, "draw"),
        ("ai_sign", AcademyManager, "ai_sign_drivers"), ("ai_renew", AcademyManager, "ai_renew_contracts")))
    monkeypatch.setattr(implementation, "academy_targets", lambda: starts)
    monkeypatch.setattr(implementation, "academy_caller_targets", lambda: callers)
    monkeypatch.setattr(implementation, "preparation_methods", lambda state, screen: {
        "academy": {"ensure_team_state": (AcademyManager.ensure_team_state, False)},
        "driver": {"active_contract": (DriverManager.active_contract, False)}})

    class Channel:
        mode = "deny"

        def __init__(self):
            self._unavailable = set()
            self.contexts = []

        def bindings(self, scope):
            return {"a.rules": ((0, "rule"),), "b.rules": ((0, "rule"),)}

        def __call__(self, contexts, selections, calls, deadline):
            assert len(contexts) == 1 and contexts[0]["scope"] == "personnel.contract"
            self.contexts.append(contexts[0])
            if self.mode == "failed":
                self._unavailable.add("b.rules")
                raise RuntimeError("Owned handler failed")
            return {owner: [{"decision": "allow"} if self.mode == "allow" or owner == "a.rules"
                            else {"decision": "deny", "message": "Owned refusal"}] for owner in calls}

    planner = SimpleNamespace(state=state, manager=manager, prepare=lambda *a: {
        "clock": {"season": 3, "absolute_week": 114}, "effective": {"season": 3}, "proposal": {}})
    monkeypatch.setattr(implementation, "AcademyContractPlanner", lambda state: planner)
    return state, screen, planner, Channel(), starts, callers


def test_academy_refusal_keeps_ai_state_rng_and_ui_selection_private(monkeypatch):
    state, screen, planner, channel, targets, callers = owned(monkeypatch)
    originals = [target.resolve({})[2] for target in (*targets, *callers)]
    adapter = implementation.RegulationAcademyAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    messages = []
    monkeypatch.setattr("hook_manager.game_adapters.academy.callers.draw_refusal", lambda owner, surface, message: messages.append(message))
    before = deepcopy((state.teams, vars(state.drivers[0]), state.driver_manager._ai_signing_query_cache, state._rng.getstate()))
    try:
        screen.handle_event(True)
        screen.handle_event(False)
        screen.draw(None)
        assert screen.selected is None and messages == ["Owned refusal"]
        state.academy_manager.ai_sign_drivers(3)
        assert channel.contexts[-1]["initiator"] == "ai"
        channel.mode = "failed"
        state.academy_manager.ai_sign_drivers(3)
        assert not state.calls
        assert before == (state.teams, vars(state.drivers[0]), state.driver_manager._ai_signing_query_cache, state._rng.getstate())
    finally:
        adapter.stop()
    assert [target.resolve({})[2] for target in (*targets, *callers)] == originals

    channel = type(channel)()
    channel.mode = "allow"
    adapter = implementation.RegulationAcademyAdapter(state, channel, lambda *a: {"selected": "current"}).install_prepared()
    try:
        state.academy_manager.ai_sign_drivers(3)
        contract = state.teams["target"]["academy"]["contracts"]["Junior"]
        state.academy_manager.ai_renew_contracts(3)
        assert state.drivers[0].academy_contract is contract and contract["term"] == 3
        assert state.calls == ["sign", "renew"] and state.teams["target"]["balance_m"] == 9.
        assert "normalized" not in state.drivers[0].contract and state.driver_manager._ai_signing_query_cache == {"live": "untouched"}
        assert state._rng.getstate() != before[-1]
        state.native_error = True
        with pytest.raises(ValueError, match="Owned native error"):
            state.academy_manager.sign_driver("target", "Junior")
        assert state.calls == ["sign", "renew", "sign"] and adapter.owners == {"a.rules": "ready", "b.rules": "ready"}
        channel.mode = "deny"
        other = type(state.academy_manager)()
        other.state = SimpleNamespace(teams={"target": {"balance_m": 10.}}, drivers=[SimpleNamespace(academy_contract=None)],
                                      calls=[], native_refuse=False, native_error=False)
        assert other.sign_driver("target", "Foreign") == (True, "Signed") and other.state.calls == ["sign"]
        assert state.calls == ["sign", "renew", "sign"]
        # Reuse one worker: rejection after leaving its scoped operation must
        # not retain the earlier allowance, and must not run a second renewal.
        channel.mode = "allow"
        outcomes = []

        def invoke():
            with adapter.operation():
                state.academy_manager.ai_renew_contracts(3)
                outcomes.append(adapter.last_outcome.status)
            state.academy_manager.ai_renew_contracts(3)
            outcomes.append(adapter.last_outcome)

        worker = threading.Thread(target=invoke)
        worker.start()
        worker.join(2)
        assert not worker.is_alive() and outcomes == ["allow", None]
        assert state.calls == ["sign", "renew", "sign", "renew"]
    finally:
        adapter.stop()


def test_academy_failed_installation_restores_all_owned_wrappers(monkeypatch):
    state, screen, planner, channel, targets, callers = owned(monkeypatch)
    original = [target.resolve({})[2] for target in (*targets, *callers)]
    register = implementation.RegulationAcademyAdapter.register

    def partial(self, owner, bindings):
        if owner == "b.rules":
            raise RuntimeError("Owned installation failure")
        return register(self, owner, bindings)

    monkeypatch.setattr(implementation.RegulationAcademyAdapter, "register", partial)
    with pytest.raises(RuntimeError, match="Owned installation failure"):
        implementation.RegulationAcademyAdapter(state, channel, None).install_prepared()
    assert [target.resolve({})[2] for target in (*targets, *callers)] == original
