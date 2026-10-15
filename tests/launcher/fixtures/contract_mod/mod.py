# SPDX-License-Identifier: MPL-2.0
"""Owned Python guest; probes should fail inside Wasm rather than obtain host authority."""

import importlib
import json
import os
from pathlib import Path
import sys
import wit_world

# componentize-py executes this inside its guest, with staged source mounted at /0.
Path("/0/build-marker.txt").write_text("built in the guest")
_modules = {}
for _name in ("socket", "subprocess", "ctypes"):
    try:
        _modules[_name] = importlib.import_module(_name)
    except ImportError:
        _modules[_name] = None
_count = 0
try:
    import packaging
except ImportError:
    _ambient_package = False
else:
    _ambient_package = True


class WitWorld(wit_world.WitWorld):
    def activate(self):
        wit_world.report("ready\n\x1b")

    def on_event(self, name, payload):
        global _count
        _count += 1
        if name == "probe":
            result = {
                "platform": sys.platform,
                "secret": os.getenv("MODLAUNCHER_TEST_SECRET", ""),
                "ambient-package": _ambient_package,
            }
            return json.dumps(result)
        if name in _modules:
            module = _modules[name]
            try:
                if module is None:
                    raise ImportError(name)
                if name == "socket":
                    module.socket()
                elif name == "ctypes":
                    module.CDLL("python313.dll")
                else:
                    module.Popen(["python", "-c", "pass"])
            except (ImportError, OSError, RuntimeError, NotImplementedError):
                return "false"
            return "true"
        if name == "write":
            Path("/0/runtime-write.txt").write_text("must not reach host files")
        if name == "import-host":
            importlib.import_module("frida")
        return json.dumps({"count": _count, "value": json.loads(payload)})
