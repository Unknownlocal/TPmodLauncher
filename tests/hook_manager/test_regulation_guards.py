# SPDX-License-Identifier: MPL-2.0
"""Shared projection, decision ordering and required-validation failure fences."""

from contextlib import contextmanager
import inspect
import sys
import threading
from types import ModuleType

import pytest

from hook_manager.callables import CallBinding, CallableTarget, ResultContract, code_digest
from hook_manager.game_adapters.regulations.channel import RegulationChannel
from hook_manager.game_adapters.regulations.guards import RegulationGuards


class Channel:
    bindings = RegulationChannel.bindings

    def __init__(self):
        self._unavailable, self.actions = set(), []
        self.mode = "allow"
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
        return {owner: [{"decision": "deny", "message": owner} if self.mode == "deny" else {"decision": "allow"}
                        for _ in rows] for owner, rows in calls.items()}


@contextmanager
def guarded(monkeypatch):
    module = ModuleType("owned_shared_regulation_guard")
    monkeypatch.setitem(sys.modules, module.__name__, module)
    channel, projections, writes = Channel(), [], []

    def native(value):
        writes.append(value)
        if value == 0:
            raise ValueError("Native failure")
        return True

    def project(arguments, result):
        projections.append(arguments["value"])
        return {"scope": "development.project", "effective": {"season": 3}, "proposal": {"value": arguments["value"]}}

    module.start = native
    engine = RegulationGuards(channel, lambda rule, season: {"selected": "current", "current": [0, 3], "upcoming": None}, "development.project")
    engine.approve(CallableTarget("start", module.__name__, (), "start", str(inspect.signature(native)), (code_digest(native.__code__),)))
    codec = ResultContract({"shape": "bool", "denied": {"literal": False}, "accepted": {"path": [], "one_of": [True]}})
    for owner in channel._owners:
        engine.register(owner, [CallBinding("project", "start", project, codec)])
    try:
        yield module, engine, channel, projections, writes
    finally:
        engine.stop()
    assert module.start is native


def test_shared_projection_ordering_native_execution_and_identified_owner_failure(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, projections, writes):
        channel.mode = "deny"
        assert module.start(12) is False and not writes and engine.last_refusal == "a.rules"
        contexts, selections, calls = channel.actions[-1]
        assert len(contexts) == len(selections) == 1 and projections == [12]
        assert calls == {"a.rules": [[0, 0, 0], [2, 0, 0]], "b.rules": [[0, 0, 0]]}
        channel.mode = "allow"
        assert module.start(4) is True and writes == [4] and projections == [12, 4]
        with pytest.raises(ValueError, match="Native failure"):
            module.start(0)
        assert writes == [4, 0] and set(engine.owners.values()) == {"ready"}
        channel.mode = "failed"
        assert module.start(1) is False
        assert engine.owners == {"a.rules": "ready", "b.rules": "unavailable"}
        attempts = len(channel.actions)
        assert module.start(2) is False and len(channel.actions) == attempts and writes == [4, 0]
        engine.remove_owner("b.rules")
        channel.mode = "allow"
        assert module.start(3) is True and set(channel.actions[-1][2]) == {"a.rules"}


def test_unenrolled_worker_cannot_bypass_shared_required_validation(monkeypatch):
    with guarded(monkeypatch) as (module, engine, channel, projections, writes):
        results = []
        thread = threading.Thread(target=lambda: results.append(module.start(1)))
        thread.start()
        thread.join(2)
        assert not thread.is_alive() and results == [False]
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
