# SPDX-License-Identifier: MPL-2.0
"""Check fixed source-owner permission tables through the real shared boundary."""

import pytest

from launcher.shared_runner_host import bind_shared_operations
from launcher.sandbox import SandboxError
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest, SourceSnapshot


OWNER = "owned.host"
CALLS = {
    "report": 'api.source_report("owned.host", "probe")',
    "add-menu-button": 'api.source_add_menu_button("owned.host", "probe", "Probe", "probe")',
    "register-settings": 'api.source_register_settings("owned.host", "{}")',
    "save-settings": 'api.source_save_settings("owned.host", "{}")',
    "register-theme": 'api.source_register_theme("owned.host", "{}")',
}


@pytest.mark.parametrize("operation", list(CALLS))
def test_ungranted_raw_source_calls_never_reach_host_providers(runner_bundle, operation):
    # Raw generated bindings must obey the same permissions as wit_world.
    call = CALLS[operation]
    code = f'''import runner_bindings as api
{call if operation == "report" else ""}
class WitWorld:
 def activate(self): pass
 def on_event(self, name, payload):
  {call if operation != "report" else "pass"}
  return payload
'''
    manifest = SourceManifest(OWNER, "1", "0.3.0", "mod", frozenset())
    snapshot = SourceSnapshot(manifest, (("mod.py", code),), len(code.encode()))
    calls, cleaned = [], []
    with pytest.raises(SandboxError, match="Capability.*required"):
        with SharedPythonRunner(runner_bundle, (snapshot,), {OWNER: {operation: lambda *args: calls.append(args)}},
                                cleanup=lambda: cleaned.append(True)) as group:
            group.activate(OWNER)
            group.on_event(OWNER, "probe", "{}")
    assert calls == [] and cleaned == [True]


def test_routing_keeps_copied_grants_and_providers_after_caller_changes():
    calls = []
    grants = {"diagnostics"}
    providers = {OWNER: {"report": calls.append}}
    tables = {OWNER: grants}
    operations = bind_shared_operations(tables, providers)
    grants.clear()
    providers[OWNER].clear()
    providers.clear()
    tables.clear()
    operations["source-report"].callback(OWNER, "probe")
    assert calls == ["probe"]
