# SPDX-License-Identifier: MPL-2.0
"""Native game support must remain usable without regulation dispatch or game imports."""

from pathlib import Path
import subprocess
import sys


def test_native_game_support_imports_without_regulation_runtime_or_game_modules():
    # A fresh process distinguishes dependencies from modules loaded by other
    # integration tests. Import every native-support module, not a fixed list.
    source = """
import importlib, pkgutil, sys
for name in ('hook_manager.game_adapters.drivers', 'hook_manager.game_adapters.projects', 'hook_manager.game_adapters.staff'):
    package = importlib.import_module(name)
    for module in pkgutil.walk_packages(package.__path__, package.__name__ + '.'):
        importlib.import_module(module.name)
for name in sys.modules:
    assert not name.startswith(('hook_manager.game_adapters.regulations', 'launcher', 'mod_runtime', 'managers.')), name
assert 'game_state' not in sys.modules
"""
    result = subprocess.run([sys.executable, "-B", "-c", source],
                            cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
