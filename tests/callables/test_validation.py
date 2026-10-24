# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for validation."""


import pytest

from hook_manager.callables import CallBinding
from hook_manager.manager import HookError

from .support import approve, boolean, pair, project


def test_projection_is_bounded_copied_and_cannot_leak_native_objects(host):
    native = {"value": [1]}
    mutations = []

    def dispatch(owner, calls, deadline):
        calls[0]["snapshot"]["value"].append(2)
        return [{"decision": "allow"}]

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", lambda args, result: native, boolean())])
    assert module.method(4) and native == {"value": [1]}
    engine.remove_owner("owner")
    engine.register("bad", [CallBinding("rule", "method", lambda args, result: {"native": object()}, boolean())])
    assert module.method(5) is False and mutations == [4]
    engine.remove_owner("bad")
    engine.register("large", [CallBinding("rule", "method", lambda args, result: {"text": "é" * 2048}, boolean())])
    assert module.method(6) is False and mutations == [4]


def test_query_preserves_native_refusal_and_original_exceptions(host):
    dispatched, original_calls = [], []
    failure = ValueError("native exception")

    def method(value):
        original_calls.append(value)
        if value == 3:
            raise failure
        return (False, "Native refusal") if value == 1 else (True, "Native allowed")

    module, engine = host(lambda *args: dispatched.append(args) or [{"decision": "deny", "message": "Rule refusal"}])
    module.method = method
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, pair(), phase="query")])
    assert module.method(1) == (False, "Native refusal") and not dispatched
    assert module.method(2) == (False, "Rule refusal") and len(dispatched) == 1
    with pytest.raises(ValueError) as caught:
        module.method(3)
    assert caught.value is failure and original_calls == [1, 2, 3]
    assert engine.owners == {"owner": "ready"}


def test_guard_calls_original_at_most_once_on_native_error(host):
    calls, error = [], ValueError("native")
    module, engine = host()

    def method(value):
        calls.append(value)
        raise error

    module.method = method
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    with pytest.raises(ValueError) as caught:
        module.method(1)
    assert caught.value is error and calls == [1] and engine.owners == {"owner": "ready"}


def test_native_result_drift_blocks_and_latches(host):
    module, engine = host()
    module.method = lambda value: {"unexpected": True}
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, boolean(), phase="query")])
    assert module.method(1) is False and engine.owners == {"owner": "unavailable"}


def test_deadline_includes_projection_dispatch_and_all_owners(host, monkeypatch):
    now, received, mutations = [0.0], [], []
    monkeypatch.setattr("hook_manager.callables.adapters.time.monotonic", lambda: now[0])

    def dispatch(owner, calls, deadline):
        received.append((owner, deadline))
        now[0] += .06
        return [{"decision": "allow"}]

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    for owner in ("a", "b"):
        engine.register(owner, [CallBinding("rule", "method", project, boolean())])
    assert module.method(1) is False and mutations == []
    assert received == [("a", .1), ("b", .1)]
    assert engine.owners == {"a": "ready", "b": "unavailable"}


def test_reentrant_dispatch_and_reconfiguration_cannot_authorize_mutation(host):
    mutations = []
    module, engine = host()
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)

    def dispatch(owner, calls, deadline):
        with pytest.raises(HookError, match="during an operation"):
            engine.remove_owner(owner)
        assert module.method(2) is False
        return [{"decision": "allow"}]

    engine.evaluate = dispatch
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
    assert module.method(1) is False and mutations == []
