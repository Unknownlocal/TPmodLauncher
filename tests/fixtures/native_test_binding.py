# SPDX-License-Identifier: MPL-2.0
"""Load Frida for owned native tests; Linux is a developer harness, not a release entry point."""

import importlib
import importlib.util
from pathlib import Path
import sys


def load(root):
    """Use the production Windows loader or a checked wheel with isolated Linux Python."""
    spec = importlib.util.spec_from_file_location(
        "native_test_dependencies", root / "launcher/bundled_dependencies.py"
    )
    dependencies = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dependencies)
    if sys.platform == "win32":
        return dependencies.import_dependencies(root)
    assert sys.platform == "linux" and sys.version_info[:2] == (3, 13)
    assert sys.flags.isolated and sys.flags.no_site
    assert not any(name == "frida" or name.startswith("frida.") for name in sys.modules)
    directory = dependencies.verify_bundle(root)
    sys.path.insert(0, str(directory))
    module = importlib.import_module("frida")
    assert (
        Path(importlib.util.find_spec("frida._frida").origin)
        .resolve()
        .is_relative_to(directory)
    )
    return module
