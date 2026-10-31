# SPDX-License-Identifier: MPL-2.0
"""Shared projection, decision ordering and required-validation failure fences."""

from contextlib import contextmanager
import inspect
import sys
import threading
from types import ModuleType

import pytest

from hook_manager.callables import CallBinding, CallableTarget, refuse_boolean
from hook_manager.game_adapters.regulations.channel import RegulationChannel
from hook_manager.game_adapters.regulations.guards import RegulationGuards


class Channel:
    bindings = RegulationChannel.bindings

    def __init__(self):
        self._unavailable, self.actions = set(), []
        self.input_requests = {}
        self.mode = "allow"
        self.message = None
        self.before_reply = lambda: None
        self._owners = {
            "a.rules": {"rules": ["native"], "bindings": [
                {"rule": "native", "scope": "development.project"},
                {"rule": "native", "scope": "personnel.contract"},
                {"rule": "native", "scope": "development.project"}]},
            "b.rules": {"rules": ["native"], "bindings": [{"rule": "native", "scope": "development.project"}]}}

    def __call__(self, contexts, selections, calls, deadline):
        self.actions.append((contexts, selections, calls))
        if self.mode == "failed":
            self._unavailable.add("b.rules")
            raise RuntimeError("Owned handler failed")
        self.before_reply()
        return {owner: [{"decision": "deny", "message": self.message or owner} if self.mode == "deny" else {"decision": "allow"}
                        for _ in rows] for owner, rows in calls.items()}


@contextmanager
def guarded(monkeypatch):
    module = ModuleType("owned_shared_regulation_guard")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    channel, projections, writes = Channel(), [], []
    error = ValueError("Native failure")

    def native(value):
        writes.append(value)
        if value == 0:
            raise error
        return value > 0

    def project(arguments, result):
        projections.append(arguments["value"])
        return {"scope": "development.project", "effective": {"season": 3}, "proposal": {"value": arguments["value"]}}

    module.start, module.error = native, error
    engine = RegulationGuards(channel, lambda rule, season: {"selected": "current", "current": [0, 3], "upcoming": None}, "development.project")
    engine.approve(CallableTarget("start", module.__name__, (), "start", str(inspect.signature(native))))
    for owner in channel._owners:
        engine.register(owner, [CallBinding("project", "start", project, refuse_boolean)])
    try:
        yield module, engine, channel, projections, writes
    finally:
        engine.stop()
    assert module.start is native


def test_closing_activation_during_a_reply_refuses_without_disabling_owners(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, _, writes):
        channel.before_reply = lambda: setattr(channel, "validation_ready", False)
        assert module.start(1) is False and not writes
        assert engine.last_outcome.status == "unavailable" and set(engine.owners.values()) == {"ready"}


def test_changed_selection_during_reply_refuses_without_disabling_healthy_mods(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, _, writes):
        current = ["open"]
        engine.policy_identity = lambda: tuple(current)
        channel.before_reply = lambda: current.__setitem__(0, "limited")
        assert module.start(1) is False and not writes
        assert engine.last_outcome.status == "unavailable" and set(engine.owners.values()) == {"ready"}
        channel.before_reply = lambda: None
        assert module.start(1) is True and writes == [1]


def test_shared_projection_ordering_native_execution_and_identified_owner_failure(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, projections, writes):
        assert engine.last_outcome is None
        channel.validation_ready = False
        engine.poll()
        assert module.start(9) is False and engine.last_outcome.status == "unavailable"
        assert not writes and not projections and not channel.actions
        assert set(engine.owners.values()) == {"ready"}
        channel.validation_ready = True
        channel.mode = "deny"
        assert module.start(12) is False and not writes and engine.last_outcome.message == "a.rules"
        assert engine.last_outcome.status == "deny"
        contexts, selections, calls = channel.actions[-1]
        assert len(contexts) == len(selections) == 1 and projections == [12]
        assert calls == {"a.rules": [[0, 0, 0], [2, 0, 0]], "b.rules": [[0, 0, 0]]}
        channel.mode = "allow"
        assert module.start(4) is True and writes == [4] and projections == [12, 4]
        assert engine.last_outcome.status == "allow"
        assert module.start(-1) is False and engine.last_outcome.status == "allow"
        with pytest.raises(ValueError, match="Native failure") as caught:
            module.start(0)
        assert caught.value is module.error
        assert writes == [4, -1, 0] and set(engine.owners.values()) == {"ready"}
        assert engine.last_outcome.status == "allow"
        # Mod messages are arbitrary display text. Even the host's usual error
        # wording must remain a policy deny when that is what the mod returned.
        channel.mode, channel.message = "deny", "Validation unavailable"
        assert module.start(5) is False and engine.last_outcome.status == "deny"
        assert engine.last_outcome.message == "Validation unavailable" and set(engine.owners.values()) == {"ready"}
        channel.mode = "failed"
        assert module.start(1) is False
        assert engine.last_outcome.status == "unavailable" and engine.last_outcome.message == "Validation unavailable"
        assert engine.owners == {"a.rules": "ready", "b.rules": "unavailable"}
        attempts = len(channel.actions)
        assert module.start(2) is False and len(channel.actions) == attempts and writes == [4, -1, 0]
        assert engine.last_outcome.status == "unavailable"
        engine.remove_owner("b.rules")
        channel.mode = "allow"
        assert module.start(3) is True and set(channel.actions[-1][2]) == {"a.rules"}
        assert engine.last_outcome.status == "allow"


def test_unscoped_worker_cannot_bypass_shared_required_validation(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, projections, writes):
        results = []
        def invoke():
            results.append((module.start(1), engine.last_outcome.status))

        thread = threading.Thread(target=invoke)
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and results == [(False, "unavailable")]
        assert engine.last_outcome is None  # Worker outcomes do not leak to the UI thread.
        assert not writes and not projections and not channel.actions
        assert set(engine.owners.values()) == {"unavailable"}


def test_shared_deadline_includes_projection_and_rejects_late_allowance(monkeypatch):
    from hook_manager.game_adapters.regulations import guards as implementation

    clock = [0.0]
    monkeypatch.setattr(implementation.time, "monotonic", lambda: clock[0])
    for phase in ("projection", "reply"):
        clock[0] = 0.0
        with guarded(monkeypatch) as (module, engine, channel, projections, writes):
            def advance():
                clock[0] += .2

            if phase == "projection":
                select = engine.select

                def slow_projection(rule, season):
                    advance()
                    return select(rule, season)

                engine.select = slow_projection
            else:
                channel.before_reply = advance
            assert module.start(1) is False and not writes
            assert len(channel.actions) == int(phase == "reply")
            assert set(engine.owners.values()) == {"unavailable"}
            assert engine.last_outcome.status == "unavailable"
