# SPDX-License-Identifier: MPL-2.0
"""Reserve loaded code or one pending compilation for a future sandbox."""

from contextlib import ExitStack

from launcher.compilation_registry import CompilationRegistry


class CompiledMod:
    """One cancellable request; matching requests share code, never mod state."""

    def __init__(self, data, timings=None, *, cache=None, executor=None):
        self.data = data
        self.closed = False
        self.cache_status = None
        self.code = self._entry = None
        try:
            if (
                type(data) is not bytes
                or not data.startswith(b"\0asm\x0d\0\x01\0")
                or len(data) > 32 * 1024 * 1024
            ):
                raise ValueError("Expected a size-limited WebAssembly Component binary")
            self._entry, self._reused = CompilationRegistry.current().request(data, timings, cache, executor)
            # A ready hit needs no worker. Direct callers without an executor
            # still compile synchronously; background loaders resume on completion.
            self.poll()
        except BaseException:
            self.close()
            raise

    def poll(self):
        """Report completion or propagate failure without waiting for a compiler."""
        if self.closed or self.data is None:
            raise RuntimeError("Compiler request is closed or already adopted")
        self._entry.registry.check_thread()
        if not self._entry.future.done():
            return False
        compiled = self._entry.future.result()
        self.engine, self.component = compiled.engine, compiled.component
        self.cache_status = ({"state": "reused", "detail": "Shared ready or pending compiled code"}
                             if self._reused else compiled.cache_status)
        return True

    def when_ready(self, wake):
        """Queue owner-thread work when compilation finishes, including ready hits."""
        self._entry.registry.check_thread()
        self._entry.future.add_done_callback(lambda _: wake())

    def take(self, data):
        """Transfer this request's reference into a sandbox lease exactly once."""
        if self.data is None or data is not self.data:
            raise ValueError("Compiler result is closed, already adopted or mismatched")
        if not self.poll():
            raise RuntimeError("Compilation is pending")
        self.code = self._entry.registry.adopt(self._entry)
        lease = ExitStack()
        lease.callback(self._entry.release)
        self._entry = self.data = None
        return lease

    def close(self):
        """Release just this request; another waiter or sandbox keeps its code."""
        if not self.closed:
            if self._entry is not None:
                self._entry.registry.check_thread()
            entry, self._entry = self._entry, None
            self.closed = True
            self.data = self.code = None
            if entry is not None:
                entry.release()
