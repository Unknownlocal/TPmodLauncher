# SPDX-License-Identifier: MPL-2.0
"""Exercise per-mod permission enforcement through the real sandbox boundary."""

import pytest
from wasmtime import wat2wasm

from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox, SandboxError
from mod_runtime.bundle import EXPORTS
from mod_runtime.contract import HOST_OPERATIONS


CALLS = {
    "report": 'api.report("probe")',
    "add-menu-button": 'api.add_menu_button("probe", "Probe", "probe")',
}


def source_for(name, phase):
    # Call the generated binding directly: the author facade is not a security boundary.
    call = CALLS[name]
    return f'''import runner_bindings as api
{call if phase == "load" else ""}
class WitWorld:
    def activate(self):
        {call if phase == "activate" else "pass"}
    def on_event(self, name, payload):
        {call if phase == "event" else "pass"}
        return payload
'''


@pytest.mark.parametrize("capabilities", [set(), {"diagnostics"}, {"main-menu"}, {"diagnostics", "main-menu"}], ids=["empty", "diagnostics", "menu", "combined"])
@pytest.mark.parametrize("operation", CALLS)
@pytest.mark.parametrize("phase", ["load", "activate", "event"])
def test_permissions_are_enforced_in_every_source_phase(runner_bundle, capabilities, operation, phase):
    calls, cleaned = [], []
    providers = {name: lambda *args, name=name: calls.append(name) for name in HOST_OPERATIONS}
    guest = Sandbox(runner_bundle.component(), bind_operations(capabilities, providers),
                    limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True))
    granted = HOST_OPERATIONS[operation].capability in capabilities
    try:
        guest.call("put-source", "mod.py", source_for(operation, phase))
        def lifecycle():
            assert guest.call("load-source", "mod") == ""
            guest.call("activate")
            assert guest.call("on-event", "probe", "{}") == "{}"
        if granted:
            lifecycle()
            assert calls == [operation]
            assert not guest.closed and cleaned == []
        else:
            with pytest.raises(SandboxError, match="Capability.*required"):
                lifecycle()
            assert calls == []
            assert guest.closed and cleaned == [True]
    finally:
        guest.close()
    assert cleaned == [True]


@pytest.mark.parametrize("operation", CALLS)
def test_grants_and_provider_snapshots_are_private_to_each_mod(runner_bundle, operation):
    calls = []
    capabilities = {HOST_OPERATIONS[operation].capability}
    providers = {operation: lambda *args: calls.append(operation)}
    allowed = bind_operations(capabilities, providers)
    denied = bind_operations(set(), providers)
    capabilities.clear()
    providers.clear()
    data = runner_bundle.component()
    with Sandbox(data, allowed, limits=PYTHON_LIMITS, required_exports=dict(EXPORTS)) as first, Sandbox(
        data, denied, limits=PYTHON_LIMITS, required_exports=dict(EXPORTS)
    ) as second:
        for guest in (first, second):
            guest.call("put-source", "mod.py", source_for(operation, "event"))
            assert guest.call("load-source", "mod") == ""
            guest.call("activate")
        assert first.call("on-event", "probe", "{}") == "{}"
        with pytest.raises(SandboxError, match="Capability.*required"):
            second.call("on-event", "probe", "{}")
        assert second.closed and not first.closed
        assert first.call("on-event", "probe", "{}") == "{}"
    assert calls == [operation, operation]


@pytest.mark.parametrize("capabilities", [{"invented"}, {"diagnostics", "invented"}, ["diagnostics"], "diagnostics", {True}])
def test_unknown_or_implicit_permissions_are_rejected(capabilities):
    with pytest.raises(SandboxError, match="capabilit"):
        bind_operations(capabilities, {})


@pytest.mark.parametrize("operation", HOST_OPERATIONS)
@pytest.mark.parametrize("provider", [None, "not callable"])
def test_granted_permissions_require_trusted_providers(operation, provider):
    with pytest.raises(SandboxError, match="requires a trusted provider"):
        bind_operations({HOST_OPERATIONS[operation].capability}, {operation: provider})


def test_unknown_providers_are_rejected():
    with pytest.raises(SandboxError, match="Unknown.*provider"):
        bind_operations(set(), {"invented": lambda: None})


def test_denied_operation_is_blocked_during_component_instantiation():
    data = wat2wasm('''(component
      (import "report" (func $report (param "message" string)))
      (core module $memory (memory (export "memory") 1))
      (core instance $memory (instantiate $memory))
      (core func $report (canon lower (func $report) (memory $memory "memory")))
      (core module $start
        (import "host" "report" (func $report (param i32 i32)))
        (func $initialize i32.const 0 i32.const 0 call $report)
        (start $initialize))
      (core instance $start (instantiate $start
        (with "host" (instance (export "report" (func $report)))))))''')
    calls, cleaned = [], []
    operations = bind_operations(set(), {"report": lambda *args: calls.append(args)})
    with pytest.raises(SandboxError, match="Capability.*required"):
        Sandbox(bytes(data), operations, cleanup=lambda: cleaned.append(True))
    assert calls == [] and cleaned == [True]


@pytest.mark.parametrize("declaration", ['(import "invented" (func))', '(import "report" (func (param "value" s32)))'])
def test_unknown_and_mistyped_imports_still_fail_admission(declaration):
    with pytest.raises(SandboxError, match="approved host operation"):
        Sandbox(bytes(wat2wasm(f"(component {declaration})")), bind_operations(set(), {}))
