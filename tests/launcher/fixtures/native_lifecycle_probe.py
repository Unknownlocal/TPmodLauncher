# SPDX-License-Identifier: MPL-2.0
"""Run the real process controller; copied beside entry.py before execution."""

import importlib.util, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


deps = load("deps", "bundled_dependencies.py")
native = sys.argv[1] == "--native"
if native:
    del sys.argv[1]
    worker = load("worker", "native_worker.py")
    frida = worker.Backend(worker.verify_worker(root))
else:
    frida = deps.import_frida(root)
lifecycle = load("lifecycle", "process_lifecycle.py")
bridge = load("bridge", "game_bridge.py")
command = lifecycle.validate_command(sys.argv[1:], Path(sys.argv[1]))
try:
    raise SystemExit(
        lifecycle.run_launcher(
            frida,
            command,
            root / ".state/controller.lock",
            root / "logs/native-test.log",
            restart_grace=0.4,
            bridge_factory=bridge.factory(root, frida),
        )
    )
finally:
    if native:
        frida.close()
