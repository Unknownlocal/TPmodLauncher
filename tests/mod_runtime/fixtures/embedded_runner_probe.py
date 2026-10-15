# SPDX-License-Identifier: MPL-2.0
"""Owned relocated-player probe: verify runners and call one without build tooling."""

import importlib.util
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
import sys
import threading

root = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("_runner_dependencies", root / "launcher/bundled_dependencies.py")
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)
dependencies.import_dependencies(root)
dependencies.load_source_packages(root)
spec = importlib.util.spec_from_file_location("mod_runtime", root / "mod_runtime/__init__.py", submodule_search_locations=[str(root / "mod_runtime")])
module = importlib.util.module_from_spec(spec)
sys.modules["mod_runtime"] = module
spec.loader.exec_module(module)

from mod_runtime import RunnerBundle
from launcher.source_mod import SourceMod
from mod_runtime.preparation import SourcePreparation
from mod_runtime.source import SourceError, SourceSnapshot

assert importlib.util.find_spec("componentize_py") is None
assert not any(".venv" in path for path in sys.path)

def audit(event, args):
    if event in ("subprocess.Popen", "socket.connect", "socket.bind", "os.system"):
        raise RuntimeError("Runner execution must not invoke build tools or network services")
sys.addaudithook(audit)

bundle = RunnerBundle(root / "libs/mod-runners")
reports, closed = [], []
mod = root / ".state/runner-probe-mod"
(mod / "src").mkdir(parents=True)
(mod / "mod.toml").write_text('''schema = 2
id = "owned.embedded"
version = "0.1.0"
api = "0.2.0"
runtime = "python"
entry = "mod"
capabilities = ["diagnostics"]
''', encoding="utf-8")
(mod / "src/mod.py").write_text('''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("source in bundled runtime")
    def on_event(self, name, payload): return payload
''' + "#" + "a" * 65536, encoding="utf-8")
try:
    SourceSnapshot.read(root / ".state/runner-linked")
except SourceError:
    pass
else:
    raise AssertionError("The bundled player must reject a junction to a valid source mod")
with ThreadPoolExecutor(max_workers=1) as executor, SourcePreparation(executor, bundle, mod) as job:
    # Waiting is only probe synchronization; the player controller will poll take().
    assert wait([job._future], timeout=20).done
    with job.take() as prepared:
        assert set(prepared.timings) == {"source_snapshot", "runner_read", "compile"}
        (mod / "src/mod.py").write_text('raise AssertionError("Changed after snapshot")', encoding="utf-8")
        with SourceMod(bundle, prepared.snapshot, prepared=prepared.compiled, timings=prepared.timings,
                       report=lambda *args: reports.append(args), cleanup=lambda: closed.append(True)) as runner:
            assert runner.sandbox._thread == threading.get_ident()
            runner.activate()
            assert runner.event("probe", {"owned": True}) == {"owned": True}
    assert prepared.snapshot is None and prepared.compiled.closed
assert reports == [("owned.embedded", "source in bundled runtime")] and closed == [True]
print("Verified relocated runner; source activation/event/cleanup passed without componentize-py")
