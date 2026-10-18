# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for ownership."""


import pytest

from hook_manager.callables import CallBinding
from hook_manager.manager import HookError, Target

from .support import approve, boolean, pair, project


def test_shared_target_order_decisions_and_owned_cleanup(host):
    received, mutations = [], []

    def dispatch(owner, calls, deadline):
        received.append((owner, calls))
        return [{"decision": "deny", "message": "Blocked"} if owner == "z.owner"
                else {"decision": "allow"} for call in calls]

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    original = module.method
    approve(engine, module)
    engine.register("z.owner", [CallBinding("rule", "method", project, boolean())])
    installed = module.method
    engine.register("a.owner", [CallBinding("z.rule", "method", project, boolean()),
                                CallBinding("a.rule", "method", project, boolean())])
    assert module.method is installed
    assert module.method(7) is False and mutations == []
    assert [owner for owner, _ in received] == ["a.owner", "z.owner"]
    assert [call["id"] for call in received[0][1]] == ["a.rule", "z.rule"]
    engine.remove_owner("z.owner")
    assert module.method is installed and module.method(8) is True and mutations == [8]
    engine.remove_owner("a.owner")
    assert module.method is original and installed(9) is True and mutations == [8, 9]
    engine.register("z.owner", [CallBinding("again", "method", project, boolean())])
    assert module.method(10) is False and mutations == [8, 9]


@pytest.mark.parametrize("decision", [None, [], [{"decision": "allow", "extra": 1}],
    [{"decision": "deny", "message": " "}], [{"decision": "deny", "message": "bad\nline"}],
    [{"decision": "deny", "message": "x" * 161}]])
def test_malformed_reply_latches_failure_and_never_unhooks(host, decision):
    calls, mutations = [], []
    module, engine = host(lambda *args: calls.append(args) or decision)
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", project, boolean())])
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
        engine.register("owner", [CallBinding(key, key, project, boolean()) for key in ("method", "second")])
    assert module.method is original and not engine.entries and not engine.owners
    engine.register("a", [CallBinding("rule", "method", project, boolean())])
    with pytest.raises(HookError, match="disagree"):
        engine.register("b", [CallBinding("rule", "method", project, pair(), phase="query")])
    assert engine.owners == {"a": "ready"}


def test_only_private_fingerprinted_approvals_and_no_registration_request(host):
    module, engine = host()
    module.method = lambda value: True
    with pytest.raises(HookError, match="fingerprinted"):
        engine.approve(Target("plain", module.__name__, (), "method", "(value)"))
    for request in ({"operation": "register"}, {"operation": "call"}, {"operation": "approve"}):
        with pytest.raises(HookError, match="Unknown"):
            engine.request(request)
    from launcher.mod_api import CAPABILITIES
    from mod_runtime.contract import HOST_OPERATIONS, SOURCE_CAPABILITIES
    assert all("callables" not in grants and "regulations" not in grants for grants in CAPABILITIES.values())
    assert all("callables" not in grants and "regulations" not in grants for grants in SOURCE_CAPABILITIES.values())
    assert set(HOST_OPERATIONS) == {"report", "add-menu-button", "register-settings", "save-settings", "register-theme"}


@pytest.mark.parametrize("fault", ["projection", "exception"])
def test_failed_owner_does_not_poison_overlapping_healthy_owners(host, fault):
    module, engine = host()
    mutations = []
    module.method = lambda value: mutations.append("method") or True
    module.second = lambda value: mutations.append("second") or True
    module.third = lambda value: mutations.append("third") or True
    for name in ("method", "second", "third"):
        approve(engine, module, name)

    def dispatch(owner, calls, deadline):
        if owner == "b":
            raise RuntimeError("Owned guest failed")
        return [{"decision": "allow"} for _ in calls]

    def broken_projection(arguments, result):
        raise ValueError("Owned projection failed")

    engine.evaluate = dispatch
    engine.register("a", [CallBinding(name, name, project, boolean()) for name in ("method", "third")])
    engine.register("b", [CallBinding(name, name,
        broken_projection if fault == "projection" else project, boolean()) for name in ("method", "second")])
    engine.register("c", [CallBinding("rule", "second", project, boolean())])
    assert module.method(1) is False
    assert engine.owners == {"a": "ready", "b": "unavailable", "c": "ready"}
    assert module.second(1) is False and engine.owners["c"] == "ready"
    assert module.third(1) is True and mutations == ["third"]
    engine.remove_owner("b")
    assert module.method(1) is True and module.second(1) is True
    assert mutations == ["third", "method", "second"]
