# SPDX-License-Identifier: MPL-2.0
"""Own one shared Python Store and its admitted source-member handles."""

from contextlib import ExitStack
from types import MappingProxyType

from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Sandbox, SandboxError
from launcher.shared_runner_host import bind_shared_operations
from .bundle import EXPORTS, RunnerBundle
from .source_transfer import RUNNER_LIMITS
from .shared_sources import SharedSources
from .source import SourceSnapshot
from .source_handle import SourceHandle
from ._source_policy import MAX_SOURCE_OWNERS


class SharedPythonRunner:
    """Create, call and close one group on its creating thread.

    Trusted callers provide owner-keyed, already attributed host providers and
    one cleanup callback for all group registrations. Members have no independent
    close operation: Python objects may be retained by any of their peers.
    """

    def __init__(self, bundle, snapshots, providers, *, cleanup=None, prepared=None, timings=None):
        self._sandbox = None
        self._limits = RUNNER_LIMITS
        ownership = ExitStack()
        try:
            if cleanup is not None:
                ownership.callback(cleanup)
            if prepared is not None:
                if type(prepared) is not CompiledMod:
                    raise SandboxError("Expected a trusted compiler result")
                ownership.callback(prepared.close)
            if type(bundle) is not RunnerBundle:
                raise SandboxError("Expected a verified runner bundle")
            if (type(snapshots) is not tuple or not 0 < len(snapshots) <= MAX_SOURCE_OWNERS
                    or any(type(snapshot) is not SourceSnapshot for snapshot in snapshots)):
                raise SandboxError("Expected a bounded tuple of source snapshots")
            manifests = {snapshot.manifest.id: snapshot.manifest for snapshot in snapshots}
            if len(manifests) != len(snapshots):
                raise SandboxError("Duplicate shared source owner")
            operations = bind_shared_operations({owner: manifest.capabilities for owner, manifest in manifests.items()}, providers)
            data = bundle.component()
            if prepared is not None:
                if prepared.data != data:
                    raise SandboxError("Compiler result differs from the verified runner")
                data = prepared.data
            self._sandbox = Sandbox(data, operations, limits=self._limits,
                                    cleanup=ownership.close, required_exports=dict(EXPORTS),
                                    prepared=prepared, timings=timings)
            SharedSources(self._sandbox, snapshots)
            self.members = MappingProxyType({owner: SourceHandle(self, manifest) for owner, manifest in manifests.items()})
        except BaseException:
            try:
                self.close()
            finally:
                ownership.close()
            raise

    @property
    def sandbox(self):
        """Expose the physical boundary to trusted framework adapters."""
        return self._sandbox

    @property
    def closed(self):
        return self._sandbox is None or self._sandbox.closed

    def _call(self, name, owner, *arguments, deadline=None):
        if owner not in self.members:
            raise SandboxError("Unknown shared source owner")
        if deadline is None:
            return self._sandbox.call(name, owner, *arguments)
        with self._sandbox.phase(fuel=self._limits.fuel, seconds=self._limits.seconds, deadline=deadline):
            return self._sandbox.call(name, owner, *arguments)

    def activate(self, owner, *, deadline=None):
        """Invoke a loaded member under its ordinary budget and optional deadline."""
        self._call("activate-source", owner, deadline=deadline)

    def on_event(self, owner, name, payload, *, deadline=None):
        """Dispatch one member's string event synchronously on the owning thread."""
        return self._call("on-source-event", owner, name, payload, deadline=deadline)

    def close(self):
        """Release the whole group once, after the active guest call has unwound."""
        if self._sandbox is not None:
            self._sandbox.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
