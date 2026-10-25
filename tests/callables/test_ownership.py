# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for ownership."""


import pytest

from hook_manager.callables import CallBinding, refuse_boolean, refuse_with_reason
from hook_manager.manager import HookError, Target

from .support import approve, project


def test_shared_target_order_decisions_and_owned_cleanup(host):
    received, mutations = [], []

    def dispatch(contexts, selections, calls, deadline):
        received.append(list(calls))
        return {owner: [{"decision": "deny", "message": "Blocked"} if owner == "z.owner"
                        else {"decision": "allow"} for _ in rows]
                for owner, rows in calls.items()}

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    original = module.method
    approve(engine, module)
    engine.register("z.owner", [CallBinding("rule", "method", project, refuse_boolean)])
    installed = module.method
    engine.register("a.owner", [CallBinding("rule", "method", project, refuse_boolean)])
    assert module.method is installed
    assert module.method(7) is False and mutations == []
    assert received == [["a.owner", "z.owner"]]
    engine.remove_owner("z.owner")
    assert module.method is installed and module.method(8) is True and mutations == [8]
    engine.remove_owner("a.owner")
    assert module.method is original and installed(9) is True and mutations == [8, 9]
    engine.register("z.owner", [CallBinding("again", "method", project, refuse_boolean)])
    assert module.method(10) is False and mutations == [8, 9]


@pytest.mark.parametrize("decision", [None, [], [{"decision": "allow", "extra": 1}],
    [{"decision": "deny", "message": " "}], [{"decision": "deny", "message": "bad\nline"}],
    [{"decision": "deny", "message": "x" * 161}]])
def test_malformed_reply_latches_failure_and_never_unhooks(host, decision):
    calls, mutations = [], []
    def dispatch(contexts, selections, batches, deadline):
        calls.append(batches)
        return None if decision is None else {owner: decision for owner in batches}

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, refuse_boolean)])
    installed = module.method
    assert module.method(1) is False and engine.owners == {"owner": "unavailable"}
    engine.poll()
    assert module.method(2) is False and module.method is installed
    assert len(calls) == 1 and mutations == []
    engine.remove_owner("owner")
    assert module.method(3) is True and mutations == [3]


def test_registration_rolls_back_and_rejects_native_conflicts(host):
    module, engine = host()
    module.method = lambda value: True
    module.second = lambda value: True
    original = module.method
    approve(engine, module)
    approve(engine, module, "second")
    module.second = lambda value, added: True
    with pytest.raises(HookError):
        engine.register("owner", [CallBinding(key, key, project, refuse_boolean) for key in ("method", "second")])
    assert module.method is original and not engine.entries and not engine.owners
    engine.register("a", [CallBinding("rule", "method", project, refuse_boolean)])
    with pytest.raises(HookError, match="disagree"):
        engine.register("b", [CallBinding("rule", "method", project, refuse_with_reason)])
    assert engine.owners == {"a": "ready"}


def test_only_private_callable_approvals_and_no_registration_request(host):
    module, engine = host()
    module.method = lambda value: True
    with pytest.raises(HookError, match="private callable"):
        engine.approve(Target("plain", module.__name__, (), "method", "(value)"))
    for request in ({"operation": "register"}, {"operation": "call"}, {"operation": "approve"}):
        with pytest.raises(HookError, match="Unknown"):
            engine.request(request)
