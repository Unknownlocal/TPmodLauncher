# SPDX-License-Identifier: MPL-2.0
"""Real resident guest protocol; each test owns a distinct lifecycle/resource proof."""

import gc

import pytest

from launcher.regulations.regulation_handler_bindings import RegulationHandlerBindings
from launcher.sandbox import SandboxError
from mod_runtime._regulations import values as values
from mod_runtime._regulations.action import RegulationAction
from mod_runtime._regulations.catalog import RegulationCatalog
from mod_runtime._regulations.contexts import RegulationContexts
from tests.regulation_support import context, make_mod, validator
from resident_support import packet, policy


@pytest.mark.parametrize("fault", ["overflow", "wrong-phase", "unbound"])
def test_bad_shared_policy_transfer_traps_and_closes_group(make_mod, fault):
    mod = make_mod("def validate(c,r): return {'decision':'allow'}\n")
    if fault == "unbound":
        with pytest.raises(SandboxError):
            mod.sandbox.call("begin-regulation-policy", 1, 1, 1)
    else:
        RegulationHandlerBindings.bind(mod.runner, {mod.manifest.id: [validator()]})
        mod.sandbox.call("begin-regulation-policy", 1, 1, 1)
        with pytest.raises(SandboxError):
            if fault == "wrong-phase":
                # This byte fits the declared size, so only the transfer-kind
                # check can reject an action chunk during policy installation.
                mod.sandbox.call("put-regulation-action", "{")
            else:
                mod.sandbox.call("put-regulation-policy", "too long")
    assert mod.sandbox.closed


def test_reused_components_are_neither_remeasured_nor_encoded_during_action_preparation(monkeypatch):
    # Trusted fixture functions stand in for admitted bindings in this accounting
    # test. Shared dispatch integration tests execute actual author source in Wasm.
    manifest, records = policy("owned.rules", count=8)
    handlers = [(lambda c, r: {"decision": "allow"}, "personnel.contract", "native")] * 8
    catalog = RegulationCatalog(manifest, {"owned.rules": handlers})
    for slot, value in enumerate(records):
        catalog.install(slot, value)
    catalog.seal()
    assert all(not gc.is_tracked(record[2].tokens) for record in catalog.records.values())
    original = values.encode

    def encoding(value):
        assert type(value) is not dict or "points" not in value
        return original(value)

    monkeypatch.setattr(values, "encode", encoding)
    action = RegulationAction(packet(manifest, 8), catalog.views["owned.rules"], "policy",
                              contexts=RegulationContexts(1, [context()]))
    for _ in range(2):
        for slot in range(4):
            function, ctx, rule = action.take(slot)
            assert function(ctx, rule) == {"decision": "allow"}
        if not action.finished:
            action.prepare()
    with pytest.raises(ValueError, match="slot"):
        action.take(0)
