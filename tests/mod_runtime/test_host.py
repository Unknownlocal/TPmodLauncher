# SPDX-License-Identifier: MPL-2.0
"""Exercise per-mod permission enforcement through the real sandbox boundary."""

from dataclasses import replace

import pytest

from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox, SandboxError
from mod_runtime.bundle import EXPORTS
from mod_runtime.contract import HOST_OPERATIONS


CALLS = {
    "report": 'api.report("probe")',
    "add-menu-button": 'api.add_menu_button("probe", "Probe", "probe")',
    "register-settings": 'api.register_settings("{}")',
    "save-settings": 'api.save_settings("{}")',
    "register-theme": 'api.register_theme("{}")',
}


def source_for(name, phase):
    # Call the generated binding directly: the author facade is not a security boundary.
    call = CALLS[name]
    return f'''import runner_bindings as api
{call if phase == "load" else ""}
class WitWorld:
    def activate(self): pass
    def on_event(self, name, payload):
        {call if phase == "event" else "pass"}
        return payload
'''


def test_all_source_operations_reach_their_granted_providers(runner_bundle):
    calls = []
    providers = {name: (lambda *args, name=name: calls.append(name) or (
        "{}" if HOST_OPERATIONS[name].result == "string" else None
    )) for name in HOST_OPERATIONS}
    operations = "\n        ".join(CALLS.values())
    source = f"""import runner_bindings as api
class WitWorld:
    def activate(self):
        {operations}
    def on_event(self, name, payload):
        return payload
"""
    cleaned = []
    with Sandbox(runner_bundle.component(), bind_operations({"diagnostics", "main-menu", "ui-settings"}, providers),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True)) as guest:
        guest.call("put-source", "mod.py", source)
        assert guest.call("load-source", "mod") == ""
        guest.call("activate")
        assert guest.call("on-event", "probe", "{}") == "{}"
        assert calls == list(CALLS)
    assert cleaned == [True]


@pytest.mark.parametrize("operation", list(CALLS))
def test_ungranted_source_operations_never_reach_host_providers(runner_bundle, operation):
    calls, cleaned = [], []
    providers = {name: lambda *args: calls.append(args) for name in HOST_OPERATIONS}
    with Sandbox(runner_bundle.component(), bind_operations(set(), providers),
                 limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: cleaned.append(True)) as guest:
        phase = "load" if operation == "report" else "event"
        guest.call("put-source", "mod.py", source_for(operation, phase))
        with pytest.raises(SandboxError, match="Capability.*required"):
            assert guest.call("load-source", "mod") == ""
            guest.call("activate")
            guest.call("on-event", "probe", "{}")
        assert guest.closed and calls == [] and cleaned == [True]


def test_grants_and_provider_snapshots_are_private_to_each_mod(runner_bundle):
    calls = []
    capabilities = {"diagnostics"}
    providers = {"report": calls.append}
    allowed = bind_operations(capabilities, providers)
    denied = bind_operations(set(), providers)
    capabilities.clear()
    providers.clear()
    data = runner_bundle.component()
    # This checks grant isolation; allow CI scheduling headroom without changing other budgets.
    limits = replace(PYTHON_LIMITS, seconds=5.0)
    with Sandbox(data, allowed, limits=limits, required_exports=dict(EXPORTS)) as first, Sandbox(
        data, denied, limits=limits, required_exports=dict(EXPORTS)
    ) as second:
        for guest in (first, second):
            guest.call("put-source", "mod.py", source_for("report", "event"))
            assert guest.call("load-source", "mod") == ""
            guest.call("activate")
        assert first.call("on-event", "probe", "{}") == "{}"
        with pytest.raises(SandboxError, match="Capability.*required"):
            second.call("on-event", "probe", "{}")
        assert second.closed and not first.closed
        assert first.call("on-event", "probe", "{}") == "{}"
    assert calls == ["probe", "probe"]
