# SPDX-License-Identifier: MPL-2.0
"""Shared Store ownership and correct host identity through real Python/Wasm."""

import json
import time

import pytest

from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import _host_operations
from launcher.sandbox import SandboxError
from mod_runtime.shared_runtime import SharedPythonRunner
from mod_runtime.source import SourceManifest
from tests.source_support import source_snapshot


ENTRY = '''class WitWorld:
 def activate(self): pass
 def on_event(self, name, payload): return payload
'''


def snapshot(owner, code=ENTRY, *, grants=(), extra=None):
    files = {"mod.py": code, **(extra or {})}
    manifest = SourceManifest(owner, "1.0.0", "0.3.0", "mod", frozenset(grants))
    return source_snapshot(manifest, files)


def providers(snapshots, calls):
    def report(owner, text): calls.append((owner, "report", text))
    def menu(owner, *args): calls.append((owner, "menu", *args))
    def settings(owner, name, text):
        calls.append((owner, name, text))
        return json.dumps({"owner": owner})
    def theme(owner, text): calls.append((owner, "theme", text))
    return {source.manifest.id: {name: operation.callback for name, operation in
            _host_operations(source.manifest, report, menu, settings, theme).items()}
            for source in snapshots}


def test_host_calls_retain_module_owner_across_imports_entries_and_peer_functions(runner_bundle):
    code = '''import wit_world
from helper import emit
PEER = wit_world.import_mod('owned.beta', 'helper')
wit_world.report('entry-import')
class WitWorld:
 def __init__(self): wit_world.report('constructor')
 def activate(self):
  wit_world.add_menu_button('item', 'Item', 'item')
  wit_world.register_settings('{}')
  wit_world.register_theme('{}')
 def on_event(self, name, payload):
  PEER.emit()
  return wit_world.save_settings('{}')
'''
    helper = "import wit_world\nwit_world.report('helper-import')\ndef emit(): wit_world.report('peer-call')\n"
    sources = tuple(snapshot(owner, code, grants=("diagnostics", "main-menu", "ui-settings"),
                             extra={"helper.py": helper}) for owner in ("owned.alpha", "owned.beta"))
    calls, cleaned = [], []
    with SharedPythonRunner(runner_bundle, sources, providers(sources, calls),
                            cleanup=lambda: cleaned.append(True)) as group:
        first, second = group.manifests
        assert calls[:3] == [("owned.alpha", "report", "helper-import"),
                             ("owned.beta", "report", "helper-import"),
                             ("owned.alpha", "report", "entry-import")]
        for owner in (first, second):
            group.activate(owner)
            assert (owner, "report", "constructor") in calls
            assert (owner, "menu", "item", "Item", "item") in calls
            assert (owner, "register-settings", "{}") in calls
            assert (owner, "theme", "{}") in calls
        assert json.loads(group.on_event(first, "peer", "{}")) == {"owner": "owned.alpha"}
        assert calls[-2:] == [("owned.beta", "report", "peer-call"),
                              ("owned.alpha", "save-settings", "{}")]
        assert not group.closed
    group.close()
    assert group.closed and cleaned == [True]


@pytest.mark.parametrize("rejected", ["foreign-owner", "ungranted-operation"])
def test_shared_provider_routing_rejects_unadmitted_calls(runner_bundle, rejected):
    call = ("import runner_bindings as raw; raw.source_report('outside.group', 'blocked')"
            if rejected == "foreign-owner" else "import wit_world; wit_world.report('blocked')")
    code = ENTRY.replace("return payload", call + "; return payload")
    sources = (snapshot("owned.alpha", grants=("diagnostics",)), snapshot("owned.beta", code))
    calls, cleaned = [], []
    with SharedPythonRunner(runner_bundle, sources, providers(sources, calls),
                            cleanup=lambda: cleaned.append(True)) as group:
        for owner in group.manifests: group.activate(owner)
        with pytest.raises(SandboxError, match="outside this runtime group|Capability.*required"):
            group.on_event("owned.beta", "probe", "{}")
        assert group.closed
    assert calls == [] and cleaned == [True]


@pytest.mark.parametrize("fault", ["fuel", "memory"])
def test_guest_resource_failure_disables_every_member_and_cleans_once(runner_bundle, fault):
    body = "while True: pass" if fault == "fuel" else "value = b'x' * (80 * 1024 * 1024)"
    code = ("class WitWorld:\n def activate(self): pass\n"
            " def on_event(self, name, payload):\n  " + body + "\n  return payload\n")
    sources = (snapshot("owned.alpha", code), snapshot("owned.beta"))
    cleaned = []
    with SharedPythonRunner(runner_bundle, sources, providers(sources, []),
                            cleanup=lambda: cleaned.append(True)) as group:
        for owner in group.manifests: group.activate(owner)
        with pytest.raises(SandboxError) as failure:
            group.on_event("owned.alpha", "probe", "{}")
        if fault == "fuel": assert "fuel" in str(failure.value)
        assert group.closed
    assert cleaned == [True]


def test_original_deadline_skips_expired_work_and_rejects_late_host_return(runner_bundle):
    code = "import wit_world\n" + ENTRY.replace("return payload", "wit_world.report('slow'); return payload")
    sources = (snapshot("owned.alpha", code, grants=("diagnostics",)), snapshot("owned.beta"))
    calls, cleaned = [], []
    operations = providers(sources, calls)
    def slow(text):
        calls.append(text)
        time.sleep(.075)
    operations["owned.alpha"]["report"] = slow
    with SharedPythonRunner(runner_bundle, sources, operations,
                            cleanup=lambda: cleaned.append(True)) as group:
        for owner in group.manifests: group.activate(owner)
        first, second = group.manifests
        with pytest.raises(SandboxError, match="expired before entry"):
            group.on_event(first, "probe", "{}", deadline=time.monotonic() - 1)
        assert calls == [] and not group.closed
        assert group.on_event(second, "probe", "{}") == "{}"
        with pytest.raises(SandboxError, match="deadline"):
            group.on_event(first, "probe", "{}", deadline=time.monotonic() + .04)
        assert group.closed
    assert calls == ["slow"] and cleaned == [True]


@pytest.mark.parametrize("stage", ["provider-admission", "source-load"])
def test_startup_failure_releases_prepared_code_and_group_cleanup(runner_bundle, stage):
    first = snapshot("owned.alpha", "import wit_world\nwit_world.report('partial')\n" + ENTRY,
                     grants=("diagnostics",))
    second = snapshot("owned.beta", "raise ValueError('broken second entry')")
    sources, calls, cleaned = (first, second), [], []
    operations = providers(sources, calls)
    if stage == "provider-admission": operations["owned.alpha"].clear()
    prepared = CompiledMod(runner_bundle.component())
    with pytest.raises(SandboxError, match="requires a trusted provider|owned.beta.*broken second entry"):
        SharedPythonRunner(runner_bundle, sources, operations, prepared=prepared,
                           cleanup=lambda: cleaned.append(True))
    assert prepared.closed and cleaned == [True]
    assert calls == ([] if stage == "provider-admission" else [("owned.alpha", "report", "partial")])
