# SPDX-License-Identifier: MPL-2.0
"""Own a Python runner from verified source transfer through lifecycle cleanup."""

from contextlib import ExitStack

from launcher.compiled_mod import CompiledMod
from launcher.runner_host import bind_operations
from launcher.sandbox import Limits, Sandbox, SandboxError
from ._source_policy import MAX_SOURCE_BYTES, MAX_SOURCE_FILES, source_path
from .bundle import EXPORTS, RunnerBundle
from .source import SourceSnapshot

# Individual transfer/import share this allowance. SharedSources uses it for
# each owner's transfer and import separately; activation/events keep 5M/one second.
SOURCE_FUEL = 256_000_000
SOURCE_SECONDS = 5.0
RUNNER_LIMITS = Limits(initialization_fuel=20_000_000, initialization_seconds=5.0, instances=64)
CHUNK_BYTES = 4096
# Each full UTF-8 chunk consumes at least 4093 bytes; each file has one tail.
MAX_TRANSFER_CALLS = MAX_SOURCE_FILES + MAX_SOURCE_BYTES // (CHUNK_BYTES - 3)


def _chunks(data):
    if not data:
        yield ""
    start = 0
    while start < len(data):
        end = min(start + CHUNK_BYTES, len(data))
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        yield data[start:end].decode("utf-8")
        start = end


class PythonRunner:
    """Load one validated snapshot; expose only activation and string events.

    Trusted callers supply a verified bundle, operation providers and optional
    compiler result. Source is transferred/imported inside Wasm during construction;
    activation remains explicit. This class does not discover packs or manage games.
    """

    def __init__(self, bundle, snapshot, providers, *, cleanup=None, prepared=None, timings=None):
        self._sandbox = None
        self._active = False
        ownership = ExitStack()
        try:
            if cleanup is not None:
                ownership.callback(cleanup)
            if prepared is not None:
                if type(prepared) is not CompiledMod:
                    raise SandboxError("Expected a trusted compiler result")
                ownership.callback(prepared.close)
            if type(bundle) is not RunnerBundle or type(snapshot) is not SourceSnapshot:
                raise SandboxError("Expected a verified runner bundle and source snapshot")
            if type(snapshot.files) is not tuple or len(snapshot.files) > MAX_SOURCE_FILES:
                raise SandboxError("Source snapshot exceeds its file limit")
            self.manifest = snapshot.manifest
            data = bundle.component()
            if prepared is not None:
                if prepared.data != data:
                    raise SandboxError("Compiler result differs from the verified runner")
                data = prepared.data  # Sandbox adopts the exact bytes owned by this result.
            self._sandbox = Sandbox(data, bind_operations(self.manifest.capabilities, providers),
                                    limits=RUNNER_LIMITS, cleanup=ownership.close,
                                    required_exports=dict(EXPORTS), prepared=prepared, timings=timings)
            with self._sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                total = calls = 0
                for path, text in snapshot.files:
                    source_path(path)
                    if type(text) is not str or len(text) > MAX_SOURCE_BYTES:
                        raise SandboxError("Source snapshot exceeds its byte limit")
                    encoded = text.encode("utf-8")
                    total += len(encoded)
                    if total > MAX_SOURCE_BYTES:
                        raise SandboxError("Source snapshot exceeds its byte limit")
                    for chunk in _chunks(encoded):
                        calls += 1
                        if calls > MAX_TRANSFER_CALLS:
                            raise SandboxError("Source transfer exceeds its call limit")
                        self._sandbox.call("put-source", path, chunk)
                error = self._sandbox.call("load-source", self.manifest.entry)
                if error:
                    raise SandboxError(("Python source load failed: " + error)[:512])
        except BaseException:
            try:
                self.close()
            finally:
                ownership.close()
            raise

    @property
    def sandbox(self):
        """Expose the owned execution boundary for trusted contract adapters."""
        return self._sandbox

    @property
    def closed(self):
        return self._sandbox is None or self._sandbox.closed

    def activate(self):
        if self._active:
            self.close()
            raise SandboxError("Python runner has already activated")
        self._sandbox.call("activate")
        self._active = True

    def on_event(self, name, payload):
        """Exchange bounded strings; the caller owns event/JSON contract validation."""
        if not self._active:
            self.close()
            raise SandboxError("Activate the Python runner before sending events")
        return self._sandbox.call("on-event", name, payload)

    def close(self):
        if self._sandbox is not None:
            self._sandbox.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
