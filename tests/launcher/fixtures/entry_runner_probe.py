# SPDX-License-Identifier: MPL-2.0
"""Run normal entry and its source scopes with an owned controller boundary."""

from contextlib import closing
import importlib.util
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
reports, progress, menus = [], [], []
bridge = SimpleNamespace(set_loading=progress.append, set_menu=lambda items: menus.append(items),
                         set_settings=lambda records, result=None: None, set_themes=lambda records: None, wake=lambda: None)
setup = entry._dependencies.load_source_packages


def run(frida, command, *args, mods_factory, **kwargs):
    assert command == [str(executable)]
    with closing(mods_factory(reports.append, bridge)) as scope:
        assert scope.runner_bundle.directory == root / "libs/mod-runners"
        scope.load(dict(state="ready", series="formula", id=pack.name, root=str(pack.resolve())))
        deadline = time.monotonic() + 30
        while scope.loading is not None:
            assert time.monotonic() < deadline
            scope.poll()
            threading.Event().wait(0.002)
        assert list(scope.mods) == ["example.diagnostics", "example.menu"]
        instances = list(scope.mods.values())
        assert all(mod.sandbox._thread == threading.get_ident() for mod in instances)
        assert scope.mods["example.diagnostics"].event("probe", {"ok": True}) == {
            "event": "probe", "received": {"ok": True},
        }
        assert progress[-1] == dict(state="complete", loaded=2, failed=0, total=2)
        assert menus[-1][0]["owner"] == "example.menu"
    assert all(mod.sandbox.closed for mod in instances)
    assert menus[-1] == []
    return 0


def load_sources(path):
    setup(path)
    from launcher import process_lifecycle
    process_lifecycle.run_launcher = run
    def audit(event, args):
        if event in ("subprocess.Popen", "socket.connect", "socket.bind", "os.system"):
            raise AssertionError("Player execution attempted a build or network operation")
    sys.addaudithook(audit)


entry._dependencies.load_source_packages = load_sources
assert entry.main(["--", str(executable)]) == 0
assert sum("CODE MOD ACTIVE:" in line for line in reports) == 2
assert "componentize_py" not in sys.modules
print("Normal entry loaded source examples and closed scopes without build tools")
