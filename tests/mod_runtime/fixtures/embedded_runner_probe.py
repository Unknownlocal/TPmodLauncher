# SPDX-License-Identifier: MPL-2.0
"""Owned relocated-player probe: verify runners and call one without build tooling."""

import importlib.util
from pathlib import Path
import sys

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
from mod_runtime.bundle import EXPORTS
from launcher.mod_contract import PYTHON_LIMITS
from launcher.runner_host import bind_operations
from launcher.sandbox import Sandbox

assert importlib.util.find_spec("componentize_py") is None
assert not any(".venv" in path for path in sys.path)

def audit(event, args):
    if event in ("subprocess.Popen", "socket.connect", "socket.bind", "os.system"):
        raise RuntimeError("Runner execution must not invoke build tools or network services")
sys.addaudithook(audit)

bundle = RunnerBundle(root / "libs/mod-runners")
reports, closed = [], []
with Sandbox(bundle.component(), bind_operations({"diagnostics"}, {"report": reports.append}),
             limits=PYTHON_LIMITS, required_exports=dict(EXPORTS), cleanup=lambda: closed.append(True)) as guest:
    guest.call("put-source", "mod.py", '''import wit_world
class WitWorld(wit_world.WitWorld):
    def activate(self): wit_world.report("source in bundled runtime")
    def on_event(self, name, payload): return payload
''')
    assert guest.call("load-source", "mod") == ""
    guest.call("activate")
    assert guest.call("on-event", "probe", '{"owned":true}') == '{"owned":true}'
assert reports == ["source in bundled runtime"] and closed == [True]
print("Verified relocated runner; source activation/event/cleanup passed without componentize-py")
