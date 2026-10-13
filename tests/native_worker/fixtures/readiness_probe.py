# SPDX-License-Identifier: MPL-2.0
"""Measure dependency setup and manager readiness on an owned embedded Python target."""

import importlib.util
import json
from pathlib import Path
import sys
import time

started = time.monotonic()
root, backend, target = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])


def load(name):
    spec = importlib.util.spec_from_file_location(
        name, root / "launcher" / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


if backend == "native":
    provider = load("native_worker")
    frida = provider.Backend(provider.verify_worker(root))
else:
    frida = load("bundled_dependencies").import_frida(root)
setup = time.monotonic() - started
lifecycle, bridge = load("process_lifecycle"), load("game_bridge")
ready = None


def report(message):
    global ready
    if message.startswith("HOOK MANAGER READY"):
        ready = time.monotonic() - started


controller = lifecycle.Controller(
    frida.get_local_device(),
    [str(target), "-B", str(Path(__file__).with_name("readiness_target.py"))],
    report,
    bridge_factory=bridge.factory(root, frida),
    restart_grace=0.2,
)
try:
    controller.start()
    deadline = time.monotonic() + 12
    while not controller.poll(timeout=0.02):
        if time.monotonic() > deadline:
            raise RuntimeError("Owned readiness measurement timed out")
finally:
    controller.close()
    if backend == "native":
        frida.close()
if ready is None or controller.cleanup_failed:
    raise RuntimeError("Manager readiness or cleanup was not confirmed")
print(json.dumps({"setup_seconds": setup, "manager_ready_seconds": ready}))
