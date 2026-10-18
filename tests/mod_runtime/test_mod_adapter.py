# SPDX-License-Identifier: MPL-2.0
"""Exercise the source-backed launcher contract through real snapshots and Wasm."""

import json

import pytest

from hook_manager.menu import MenuRequests
from launcher.sandbox import SandboxError
from launcher.source_mod import SourceMod
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


def test_invalid_guest_json_disables_source_contract_and_cleans(runner_bundle, source):
    result = "{"
    code = CODE.replace('json.dumps({"count": self.count, "name": name, "value": json.loads(payload)})', repr(result))
    cleaned = []
    with SourceMod(runner_bundle, source(code), report=lambda *_: None, cleanup=lambda: cleaned.append(True)) as mod:
        mod.activate()
        with pytest.raises(SandboxError):
            mod.event("owned.event", None)
        assert mod.sandbox.failure and mod.sandbox.closed and cleaned == [True]


def test_invalid_event_input_closes_before_guest_callback(runner_bundle, source):
    name, value = "owned.event", "\u6c34" * 1400
    code = CODE.replace("self.count += 1", 'wit_world.report("event reached")\n        self.count += 1')
    reports, cleaned = [], []
    with SourceMod(runner_bundle, source(code), report=lambda *args: reports.append(args),
                   cleanup=lambda: cleaned.append(True)) as mod:
        mod.activate()
        reports.clear()
        with pytest.raises(SandboxError):
            mod.event(name, value)
        assert reports == [] and cleaned == [True] and mod.sandbox.closed


def test_partial_menu_declarations_are_revoked_after_failure(runner_bundle, source):
    code = '''import wit_world
class WitWorld:
    def activate(self):
        wit_world.add_menu_button("probe", "Probe", "probe")
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
