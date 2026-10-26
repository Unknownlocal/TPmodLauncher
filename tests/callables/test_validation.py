# SPDX-License-Identifier: MPL-2.0
"""Private callable checks for validation."""


import pytest

from hook_manager.callables import CallBinding, refuse_boolean
from hook_manager.manager import HookError

from .support import approve, project


def test_projection_is_copied_and_cannot_leak_native_objects(host):
    native = {"effective": {"season": 1}, "proposal": {"value": [1]}}
    mutations = []

    def dispatch(contexts, selections, calls, deadline):
        contexts[0]["proposal"]["value"].append(2)
        return {owner: [{"decision": "allow"}] for owner in calls}

    module, engine = host(dispatch)
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)
    engine.register("owner", [CallBinding("rule", "method", lambda args, result: native, refuse_boolean)])
    assert module.method(4) and native["proposal"] == {"value": [1]}
    engine.remove_owner("owner")
    engine.register("bad", [CallBinding("rule", "method", lambda args, result: {"effective": {"season": 1}, "proposal": {"value": [object()]}}, refuse_boolean)])
    assert module.method(5) is False and mutations == [4]


def test_reentrant_dispatch_and_reconfiguration_cannot_authorize_mutation(host):
    mutations = []
    module, engine = host()
    module.method = lambda value: mutations.append(value) or True
    approve(engine, module)

    def dispatch(contexts, selections, calls, deadline):
        with pytest.raises(HookError, match="during an operation"):
            engine.remove_owner("owner")
        assert module.method(2) is False
        return {owner: [{"decision": "allow"}] for owner in calls}

    engine.channel.dispatch = dispatch
    engine.register("owner", [CallBinding("rule", "method", project, refuse_boolean)])
    assert module.method(1) is False and mutations == []
