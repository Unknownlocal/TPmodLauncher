# SPDX-License-Identifier: MPL-2.0
"""Drive the private callable acceptance helper through the real native bridge."""

import importlib.util
import json
from pathlib import Path
import sys
import time

root = Path(__file__).resolve().parents[1]
output = Path(sys.argv[3])


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, root / "launcher" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


frida = load("callable_binding", "native_test_binding.py").load(root)
# Isolated Python needs the trusted package locations before sibling imports.
load("dependencies", "bundled_dependencies.py").load_source_packages(root)
from launcher import process_lifecycle as lifecycle, game_bridge as bridge
messages = []
controller = lifecycle.Controller(
    frida.get_local_device(), [sys.argv[1], "-B", sys.argv[2], str(output)],
    messages.append, bridge_factory=bridge.factory(root, frida), restart_grace=.2,
)
try:
    controller.start()
    deadline = time.monotonic() + 12
    while not (output / "active.json").exists() and time.monotonic() < deadline:
        controller.poll(timeout=.02)
    assert (output / "active.json").exists(), messages
    (output / "shutdown-request").touch()
    while not (output / "shutdown-ready").exists() and time.monotonic() < deadline:
        controller.poll(timeout=.02)
    assert (output / "shutdown-ready").exists(), messages
finally:
    controller.close()
assert not controller.cleanup_failed, messages
deadline = time.monotonic() + 5
while not (output / "closed.json").exists() and time.monotonic() < deadline:
    time.sleep(.02)
assert (output / "closed.json").exists(), messages
print(json.dumps(messages))
