# SPDX-License-Identifier: MPL-2.0
"""Load a real Python mod through the contract using the shipped, verified interpreter."""

from contextlib import closing
import importlib.util
from pathlib import Path
import sys

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "test_dependencies", root / "launcher/bundled_dependencies.py"
)
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)
dependencies.import_dependencies(root)
# Use the same source setup as entry.py; no source or builder paths enter sys.path.
dependencies.load_source_packages(root)
from launcher.pack_mods import PackMods

messages = []
with closing(PackMods(Path(sys.argv[1]), messages.append)) as scope:
    folder = Path(sys.argv[1]) / "data/owned pack"
    scope.load(
        {
            "state": "ready",
            "series": "formula",
            "id": folder.name,
            "root": str(folder.resolve()),
        }
    )
    mod = scope.mods["example.diagnostics"]
    assert mod.event("echo", {"number": 42}) == {"count": 1, "value": {"number": 42}}
assert any(
    '"id": "example.diagnostics", "message": "ready  "' in line for line in messages
)
assert mod.sandbox.closed
assert importlib.util.find_spec("componentize_py") is None
print("Python author contract and pack loading passed")
