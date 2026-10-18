# SPDX-License-Identifier: MPL-2.0
"""Apply the launcher's existing mod contract to a source-backed Python runner."""

from contextlib import ExitStack

from launcher.compiled_mod import CompiledMod
from launcher.mod_contract import Mod, _host_operations
from launcher.sandbox import SandboxError
from mod_runtime.runtime import PythonRunner
from mod_runtime.source import SourceSnapshot


class SourceMod(Mod):
    """Own one source mod with the same identity, JSON and lifecycle policies as Mod.

    Trusted callers prepare the bundle/snapshot and supply host providers. Pack
    discovery, background preparation and session readiness remain caller work.
    """

    def __init__(self, bundle, snapshot, *, report=None, menu=None, settings=None, theme=None, cleanup=None,
                 timings=None, prepared=None):
        ownership = ExitStack()
        try:
            if cleanup is not None:
                ownership.callback(cleanup)
            if prepared is not None:
                if type(prepared) is not CompiledMod:
                    raise SandboxError("Expected a trusted compiler result")
                ownership.callback(prepared.close)
            if type(snapshot) is not SourceSnapshot:
                raise SandboxError("Expected a validated source snapshot")
            operations = _host_operations(snapshot.manifest, report, menu, settings, theme)
            self.runner = PythonRunner(
                bundle, snapshot, {name: operation.callback for name, operation in operations.items()},
                cleanup=ownership.close, timings=timings, prepared=prepared,
            )
        except BaseException:
            ownership.close()
            raise
        self.manifest = snapshot.manifest
        # Only regulation owners need retained host text for later handler
        # binding; other mods do not keep another source snapshot alive.
        # Never reopen an author's mutable src/ tree after activation.
        self.snapshot = snapshot if "regulations" in snapshot.manifest.capabilities else None
        self.sandbox = self.runner.sandbox
        self._active = False

    def _activate(self):
        self.runner.activate()

    def _on_event(self, name, payload):
        return self.runner.on_event(name, payload)

    def close(self):
        self.runner.close()
