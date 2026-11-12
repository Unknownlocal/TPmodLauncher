# SPDX-License-Identifier: MPL-2.0
"""Run normal entry and its source scopes with an owned controller boundary."""

from contextlib import closing
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from types import SimpleNamespace

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_owned_entry", root / "launcher/entry.py")
entry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entry)
assert importlib.util.find_spec("componentize_py") is None
assert not any(".venv" in path for path in sys.path)
game = root.parent
executable = game / "TeamPrincipal.exe"
executable.write_bytes(b"owned inert fixture, never executed")
pack = game / "data/entry-owned-pack"
for example in ("diagnostic_mod", "menu_mod"):
    shutil.copytree(root / "examples" / example, pack / example)
settings_mod = pack / "owned.source"
(settings_mod / "src").mkdir(parents=True)
(settings_mod / "mod.toml").write_text('''schema = 2
id = "owned.settings"
version = "0.1.0"
api = "0.3.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "ui-settings"]
''', encoding="utf-8")
definition = {"schema": 1, "label": "Owned UI", "fields": [
    {"id": "accent", "label": "Accent", "color": "#112233"}]}
(settings_mod / "src/mod.py").write_text(f'''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.report("source in bundled runtime")
        wit_world.register_settings({json.dumps(json.dumps(definition))})
    def on_event(self, name, payload):
        if name == "settings.save": return wit_world.save_settings(payload)
        return payload
''' + "#" + "a" * 65536, encoding="utf-8")
reports, progress, menus = [], [], []
bridge = SimpleNamespace(set_loading=progress.append,
                         set_ui=lambda snapshot: menus.append(snapshot["menu"]) if "menu" in snapshot else None,
                         wake=lambda: None)
setup = entry._dependencies.load_source_packages


def run(frida, command, *args, mods_factory, **kwargs):
    assert command == [str(executable), "owned-argument"]
    with closing(mods_factory(reports.append, bridge)) as scope:
        assert scope.runner_bundle.directory == root / "libs/mod-runners"
        scope.load(dict(state="ready", series="formula", id=pack.name, root=str(pack.resolve())))
        deadline = time.monotonic() + 30
        while scope.loading is not None:
            assert time.monotonic() < deadline
            scope.poll()
            threading.Event().wait(0.002)
        assert list(scope.mods) == ["example.diagnostics", "example.menu", "owned.settings"]
        instances = list(scope.mods.values())
        assert all(mod.sandbox._thread == threading.get_ident() for mod in instances)
        assert scope.mods["example.diagnostics"].event("probe", {"ok": True}) == {
            "event": "probe", "received": {"ok": True},
        }
        settings = scope.mods["owned.settings"]
        assert settings.event("probe", {"owned": True}) == {"owned": True}
        assert scope.settings.records[0]["fields"][0]["label"] == "Accent"
        assert not (settings_mod / "settings.json").exists()
        saved = {"schema": 1, "values": {"accent": "#ABCDEF"}}
        assert settings.event("settings.save", saved) == saved
        assert json.loads((settings_mod / "settings.json").read_text()) == saved
        assert progress[-1] == dict(state="complete", loaded=3, failed=0, total=3)
        assert menus[-1][0]["owner"] == "example.menu"
    assert all(mod.sandbox.closed for mod in instances)
    assert menus[-1] == []
    assert scope.settings.records == []
    from launcher.ui_settings import UISettings
    assert json.loads(UISettings(settings_mod).register(json.dumps(definition))) == saved
    assert any('"message": "source in bundled runtime"' in line for line in reports)
    return 0


def load_sources(path):
    setup(path)
    from launcher import process_lifecycle
    from mod_runtime.source import SourceError, SourceSnapshot
    try:
        SourceSnapshot.read(root / ".state/runner-linked")
    except SourceError:
        pass
    else:
        raise AssertionError("Bundled source loading accepted a Windows junction")
    owner, read = threading.get_ident(), SourceSnapshot.read
    def snapshot(folder):
        assert threading.get_ident() != owner
        result = read(folder)
        # Execution must use captured bytes, including a source spanning chunks.
        (Path(folder) / "src/mod.py").write_text('raise AssertionError("Changed after snapshot")')
        return result
    SourceSnapshot.read = snapshot
    process_lifecycle.run_launcher = run
    def audit(event, args):
        if event in ("subprocess.Popen", "socket.connect", "socket.bind", "os.system"):
            raise AssertionError("Player execution attempted a build or network operation")
    sys.addaudithook(audit)


entry._dependencies.load_source_packages = load_sources
assert entry.main(["--", str(executable), "owned-argument"]) == 0
assert sum("CODE MOD ACTIVE:" in line for line in reports) == 3
assert "componentize_py" not in sys.modules
print("Normal entry loaded source examples and closed scopes without build tools")
