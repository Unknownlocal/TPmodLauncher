# SPDX-License-Identifier: MPL-2.0
"""Exercise the source-backed launcher contract through real snapshots and Wasm."""

import json
from pathlib import Path

import pytest

from hook_manager.menu import MenuRequests
from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import ManifestError
from launcher.sandbox import SandboxError
from launcher.source_mod import SourceMod
from launcher import source_mod
from mod_runtime.source import SourceSnapshot


CODE = '''import json
import wit_world
class WitWorld(wit_world.WitWorld):
    def __init__(self): self.count = 0
    def activate(self): wit_world.report("ready\\n\\r\\t")
    def on_event(self, name, payload):
        self.count += 1
        return json.dumps({"count": self.count, "name": name, "value": json.loads(payload)})
'''


@pytest.fixture
def source(tmp_path):
    def read(code=CODE, *, capabilities=("diagnostics",), mod_id="owned.source"):
        folder = tmp_path / mod_id
        (folder / "src").mkdir(parents=True)
        (folder / "mod.toml").write_text(f'''schema = 2
id = "{mod_id}"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = {json.dumps(capabilities)}
''', encoding="utf-8")
        (folder / "src/mod.py").write_text(code, encoding="utf-8")
        # A stale author component must not influence source-backed admission.
        (folder / "mod.wasm").write_bytes(b"unusable author component")
        return SourceSnapshot.read(folder)
    return read


@pytest.mark.parametrize("example", ["diagnostic_mod", "menu_mod"])
def test_existing_examples_use_launcher_identity_menu_and_json_contract(runner_bundle, source, example):
    code = (Path(__file__).resolve().parents[2] / "examples" / example / "src/mod.py").read_text(encoding="utf-8")
    grants = ("diagnostics", "main-menu") if example == "menu_mod" else ("diagnostics",)
    snapshot = source(code, capabilities=grants)
    messages, cleaned = [], []
    menu = MenuRequests()
    def cleanup():
        menu.remove(snapshot.manifest.id)
        cleaned.append(True)
    with SourceMod(runner_bundle, snapshot, report=lambda *args: messages.append(args),
                   menu=menu.add, cleanup=cleanup) as mod:
        assert messages == [] and menu.buttons == []
        mod.activate()
        assert messages == [("owned.source", "Menu example activated" if example == "menu_mod" else "Diagnostic example activated")]
        result = mod.event("owned.event", {"number": 42})
        if example == "menu_mod":
            assert result is None and len(menu.buttons) == 1
            assert menu.buttons[0]["owner"] == snapshot.manifest.id
        else:
            assert result == {"event": "owned.event", "received": {"number": 42}}
    mod.close()
    assert mod.sandbox.closed and cleaned == [True] and menu.buttons == []


def test_source_instances_keep_state_and_host_owned_identity_separate(runner_bundle, source):
    reports = []
    with SourceMod(runner_bundle, source(), report=lambda *args: reports.append(args)) as first, SourceMod(
        runner_bundle, source(mod_id="owned.second"), report=lambda *args: reports.append(args)
    ) as second:
        with pytest.raises(SandboxError, match="Activate"):
            first.event("owned.event", None)
        first.activate()
        second.activate()
        with pytest.raises(SandboxError, match="already"):
            first.activate()
        assert reports == [("owned.source", "ready   "), ("owned.second", "ready   ")]
        value = {"unicode": "\u00e9\u6c34", "values": [True, None, 42, 0.25]}
        assert first.event("owned.event", value) == {"count": 1, "name": "owned.event", "value": value}
        value["values"].append("changed after delivery")
        assert first.event("owned.event", None)["count"] == 2
        assert second.event("owned.event", False)["count"] == 1
        first.close()
        assert second.event("owned.event", None)["count"] == 2


@pytest.mark.parametrize("result", ["{", "NaN", "9223372036854775808", "[" * 10 + "0" + "]" * 10])
def test_invalid_guest_json_disables_source_contract_and_cleans(runner_bundle, source, result):
    code = CODE.replace('json.dumps({"count": self.count, "name": name, "value": json.loads(payload)})', repr(result))
    cleaned = []
    with SourceMod(runner_bundle, source(code), report=lambda *_: None, cleanup=lambda: cleaned.append(True)) as mod:
        mod.activate()
        with pytest.raises(SandboxError):
            mod.event("owned.event", None)
        assert mod.sandbox.failure and mod.sandbox.closed and cleaned == [True]


@pytest.mark.parametrize("name,value", [
    ("../invalid", None),
    ("owned.event", object()),
    ("owned.event", float("nan")),
    ("owned.event", 2**63),
    ("owned.event", "a" * 4097),
    ("owned.event", "\u6c34" * 1400),
], ids=["name", "object", "nonfinite", "integer", "string", "utf8-bytes"])
def test_invalid_event_input_closes_before_guest_callback(runner_bundle, source, name, value):
    code = CODE.replace("self.count += 1", 'wit_world.report("event reached")\n        self.count += 1')
    reports, cleaned = [], []
    with SourceMod(runner_bundle, source(code), report=lambda *args: reports.append(args),
                   cleanup=lambda: cleaned.append(True)) as mod:
        mod.activate()
        reports.clear()
        with pytest.raises(SandboxError):
            mod.event(name, value)
        assert reports == [] and cleaned == [True] and mod.sandbox.closed


def test_denied_source_operations_never_reach_supplied_providers(runner_bundle, source):
    code = '''import runner_bindings as api
api.report("ungranted")
class WitWorld:
    def activate(self): pass
    def on_event(self, name, payload):
        return payload
'''
    calls, cleaned = [], []
    snapshot = source(code, capabilities=())
    with pytest.raises(SandboxError, match="Capability.*required"):
        with SourceMod(runner_bundle, snapshot, report=lambda *args: calls.append(args),
                       menu=lambda *args: calls.append(args), cleanup=lambda: cleaned.append(True)) as mod:
            mod.activate()
            mod.event("owned.event", None)
    assert calls == [] and cleaned == [True]


@pytest.mark.parametrize("capability", ["diagnostics", "main-menu"])
def test_provider_admission_failures_release_prepared_work_before_runner_entry(runner_bundle, source, capability):
    compiled = CompiledMod(runner_bundle.component())
    cleaned = []
    with pytest.raises(ManifestError, match="trusted"):
        SourceMod(runner_bundle, source(capabilities=(capability,)), prepared=compiled,
                  cleanup=lambda: cleaned.append(True))
    assert compiled.closed and cleaned == [True]


@pytest.mark.parametrize("phase", ["load", "activate", "event"])
def test_partial_menu_declarations_are_revoked_after_failure(runner_bundle, source, phase):
    setup = 'wit_world.add_menu_button("probe", "Probe", "probe")'
    code = f'''import wit_world
{setup + '; raise ValueError("failed after menu declaration")' if phase == "load" else ""}
class WitWorld:
    def activate(self):
        {setup}
        {"raise ValueError('failed activation')" if phase == "activate" else "pass"}
    def on_event(self, name, payload):
        raise ValueError("failed event")
'''
    snapshot = source(code, capabilities=("main-menu",))
    menu, cleaned = MenuRequests(), []
    def cleanup():
        assert menu.buttons  # Prove a declaration existed before rollback.
        menu.remove(snapshot.manifest.id)
        cleaned.append(True)
    with pytest.raises(SandboxError):
        with SourceMod(runner_bundle, snapshot, menu=menu.add, cleanup=cleanup) as mod:
            mod.activate()
            mod.event("owned.event", None)
    assert menu.buttons == [] and cleaned == [True]


def test_admission_interruption_runs_cleanup_and_releases_compiler(runner_bundle, source, monkeypatch):
    snapshot = source()
    compiled = CompiledMod(runner_bundle.component())
    cleaned = []
    def interrupt(*args):
        raise KeyboardInterrupt("owned admission interruption")
    monkeypatch.setattr(source_mod, "_host_operations", interrupt)
    with pytest.raises(KeyboardInterrupt, match="owned admission interruption"):
        SourceMod(runner_bundle, snapshot, prepared=compiled, cleanup=lambda: cleaned.append(True))
    assert compiled.closed and cleaned == [True]


@pytest.mark.parametrize("failure", ["snapshot", "syntax", "interrupt"])
def test_admission_and_import_failures_release_contract_cleanup(runner_bundle, source, failure):
    compiled = CompiledMod(runner_bundle.component())
    cleaned = []
    snapshot = None if failure == "snapshot" else source("def broken(:" if failure == "syntax" else 'import wit_world; wit_world.report("load")')
    def report(*args):
        raise KeyboardInterrupt("owned host interruption")
    # Guest host callbacks convert interruption into a sandbox failure; admission
    # outside guest execution still preserves BaseException in the ownership stack.
    with pytest.raises(SandboxError):
        SourceMod(runner_bundle, snapshot, report=report, prepared=compiled, cleanup=lambda: cleaned.append(True))
    assert compiled.closed and cleaned == [True]
