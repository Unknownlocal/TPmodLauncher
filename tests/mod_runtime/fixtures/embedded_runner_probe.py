# SPDX-License-Identifier: MPL-2.0
"""Owned relocated-player probe: verify runners and call one without build tooling."""

import importlib.util
from contextlib import closing
import json
from pathlib import Path
import sys
import threading
import time

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_runner_dependencies", root / "launcher/bundled_dependencies.py")
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)
dependencies.import_dependencies(root)
dependencies.load_source_packages(root)

from mod_runtime import RunnerBundle
from launcher.pack_mods import PackMods
from mod_runtime.source import SourceError, SourceSnapshot

assert importlib.util.find_spec("componentize_py") is None
assert not any(".venv" in path for path in sys.path)

def audit(event, args):
    if event in ("subprocess.Popen", "socket.connect", "socket.bind", "os.system"):
        raise RuntimeError("Runner execution must not invoke build tools or network services")
sys.addaudithook(audit)

bundle = RunnerBundle(root / "libs/mod-runners")
reports, progress = [], []
game = root / ".state/runner-game"
pack = game / "data/owned pack"
mod = pack / "owned.source"
(mod / "src").mkdir(parents=True)
(mod / "mod.toml").write_text('''schema = 2
id = "owned.embedded"
version = "0.1.0"
api = "0.3.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics", "ui-settings"]
''', encoding="utf-8")
(mod / "src/mod.py").write_text('''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.report("source in bundled runtime")
        wit_world.register_settings('{"schema":1,"label":"Owned UI","fields":[{"id":"accent","label":"Accent","color":"#112233"}]}')
    def on_event(self, name, payload):
        if name == "settings.save": return wit_world.save_settings(payload)
        return payload
''' + "#" + "a" * 65536, encoding="utf-8")
try:
    SourceSnapshot.read(root / ".state/runner-linked")
except SourceError:
    pass
else:
    raise AssertionError("The bundled player must reject a junction to a valid source mod")
owner = threading.get_ident()
read = SourceSnapshot.read
def snapshot(folder):
    assert threading.get_ident() != owner
    result = read(folder)
    (Path(folder) / "src/mod.py").write_text('raise AssertionError("Changed after snapshot")', encoding="utf-8")
    return result
SourceSnapshot.read = snapshot
with closing(PackMods(game, reports.append, runner_bundle=bundle, set_loading=progress.append)) as scope:
    scope.load(dict(state="ready", series="formula", id=pack.name, root=str(pack.resolve())))
    deadline = time.monotonic() + 20
    while scope.loading is not None:
        assert time.monotonic() < deadline
        scope.poll()
        time.sleep(0.002)
    runner = scope.mods["owned.embedded"]
    assert runner.sandbox._thread == owner
    assert runner.event("probe", {"owned": True}) == {"owned": True}
    assert scope.settings.records[0]["fields"][0]["label"] == "Accent"
    assert not (mod / "settings.json").exists()
    saved_settings = {"schema": 1, "values": {"accent": "#ABCDEF"}}
    assert runner.event("settings.save", saved_settings) == saved_settings
    assert json.loads((mod / "settings.json").read_text(encoding="utf-8")) == saved_settings
    assert progress[-1] == dict(state="complete", loaded=1, failed=0, total=1)
    timing = next(json.loads(line.split(": ", 1)[1])["phase_seconds"] for line in reports if line.startswith("CODE MOD TIMING:"))
    assert {"source_snapshot", "runner_read", "compile", "source_load", "activate"} <= timing.keys()
assert runner.sandbox.closed
assert scope.settings.records == []
from launcher.ui_settings import UISettings
fresh = UISettings(mod)
assert json.loads(fresh.register('{"schema":1,"label":"Owned UI","fields":[{"id":"accent","label":"Accent","color":"#112233"}]}')) == saved_settings
assert any('"message": "source in bundled runtime"' in line for line in reports)
print("Verified relocated runner; source and UI settings save/load/cleanup passed without componentize-py")
