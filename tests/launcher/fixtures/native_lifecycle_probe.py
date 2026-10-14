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


frida = load("test_binding", "native_test_binding.py").load(root)
lifecycle = load("lifecycle", "process_lifecycle.py")
bridge = load("bridge", "game_bridge.py")
command = lifecycle.validate_command(sys.argv[1:], Path(sys.argv[1]))
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
