# SPDX-License-Identifier: MPL-2.0
"""Load a real Python mod through the contract using the shipped, verified interpreter."""

import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
# Only these two trusted source packages gain import paths, after dependency verification.
for name in ("launcher", "hook_manager"):
    spec = importlib.util.spec_from_file_location(
        name, root / name / "__init__.py", submodule_search_locations=[str(root / name)]
    )
    package = importlib.util.module_from_spec(spec)
    sys.modules[name] = package
    spec.loader.exec_module(package)
from launcher.bundled_dependencies import import_dependencies

import_dependencies(root)
from launcher.mod_contract import Manifest, Mod

messages = []
with Mod(
    Manifest.read(sys.argv[1]), report=lambda *message: messages.append(message)
) as mod:
    mod.activate()
    assert mod.event("echo", {"number": 42}) == {"count": 1, "value": {"number": 42}}
assert messages == [("example.diagnostics", "ready  ")]
assert mod.sandbox.closed
print("Python author contract passed")
