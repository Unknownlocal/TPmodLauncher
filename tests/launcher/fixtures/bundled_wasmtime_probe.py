# SPDX-License-Identifier: MPL-2.0
"""Exercise the sandbox host on bundled Python with its verified native dependencies."""

import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bundled_dependencies", root / "launcher/bundled_dependencies.py"
)
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)
dependencies.import_dependencies(root)

# Use the launcher's controlled source paths, including the sandbox's helper modules.
dependencies.load_source_packages(root)
from launcher import sandbox as host
from launcher.compilation_cache import CompilationCache
from launcher.compiled_mod import CompiledMod
from wasmtime import wat2wasm

component = bytes(wat2wasm(Path(__file__).with_name("sandbox_guest.wat").read_text()))
grants = {"bonus": host.Operation(("s32",), "s32", lambda value: value + 6)}
cleaned = []
cache = CompilationCache(root)
prepared = CompiledMod(component, cache=cache)
assert prepared.cache_status["state"] == "enabled", prepared.cache_status
with host.Sandbox(
    component, grants, cleanup=lambda: cleaned.append(True), prepared=prepared
) as sandbox:
    assert sandbox.call("apply", 36) == 42
    try:
        sandbox.call("trap")
    except host.SandboxError:
        pass
    else:
        raise AssertionError("Guest trap was not rejected")
    assert sandbox.closed and cleaned == [True]
prepared.close()
# A second compilation uses the same native cache but creates fresh guest state.
prepared = CompiledMod(component, cache=cache)
with host.Sandbox(component, grants, prepared=prepared) as sandbox:
    assert sandbox.call("counter") == 1
prepared.close()
print("Component returned 42")
