# SPDX-License-Identifier: MPL-2.0
"""Find loaded code or join its pending compilation before allocating a copy.

One execution thread owns each registry. Only compilation runs elsewhere; that
worker never creates a Store. The lock also lets a completed, abandoned compiler
result be freed without waiting for another controller poll. Validation calls do
not use this registry lock; it covers preparation, adoption and resource release.
"""

from concurrent.futures import Future
import hashlib
import threading

from launcher.compilation import Compilation
from launcher.compiled_code import CompiledCode
from launcher.compiled_component import COMPILER_SETTINGS, CompiledComponent


class CompilationRegistry:
    _local = threading.local()

    def __init__(self):
        self.thread = threading.get_ident()
        self.entries = {}
        # Future.cancel() may invoke the completion callback immediately on this
        # thread, so that callback must be able to take the same lock again.
        self.lock = threading.RLock()

    @classmethod
    def current(cls):
        if not hasattr(cls._local, "registry"):
            cls._local.registry = cls()
        return cls._local.registry

    def check_thread(self):
        if threading.get_ident() != self.thread:
            raise RuntimeError("Use compilation requests on their execution thread")

    def request(self, data, timings, cache, executor):
        self.check_thread()
        # This digest identifies identical portable code, not game compatibility.
        key = (hashlib.sha256(data).digest(), COMPILER_SETTINGS)
        with self.lock:
            entry = self.entries.get(key)
            if entry is not None:
                entry.users += 1
                return entry, True
            entry = Compilation(self, key)
            entry.users = 1
            self.entries[key] = entry
            try:
                if executor is None:
                    entry.future = Future()
                else:
                    entry.future = executor.submit(CompiledComponent, data, timings, cache=cache)
            except BaseException:
                self.entries.pop(key)
                raise
        if executor is None:
            try:
                entry.future.set_result(CompiledComponent(data, timings, cache=cache))
            except BaseException as error:
                entry.future.set_exception(error)
        entry.future.add_done_callback(lambda _: self.finished(entry))
        return entry, False

    def adopt(self, entry):
        """Transfer native ownership once, on the future Store's execution thread."""
        self.check_thread()
        with self.lock:
            if not entry.future.done():
                raise RuntimeError("Compilation is pending")
            compiled = entry.future.result()
            if entry.code is None:
                entry.code = CompiledCode(compiled.engine, compiled.component,
                                          compiled.resources, self.thread)
            return entry.code

    def release(self, entry):
        self.check_thread()
        with self.lock:
            entry.users -= 1
            if not entry.users:
                # Cancelling a queued compiler is safe only while there are no
                # other requests. A running compiler drains and is freed later.
                entry.future.cancel()
                self._dispose(entry)

    def finished(self, entry):
        with self.lock:
            self._dispose(entry)

    def _dispose(self, entry):
        # A queued cancellation can reach here twice before release() resumes.
        # Check membership before the Future, which the first disposal clears.
        if entry.users or self.entries.get(entry.key) is not entry or not entry.future.done():
            return
        self.entries.pop(entry.key)
        try:
            if entry.code is not None:
                entry.code.close()
            elif not entry.future.cancelled() and entry.future.exception() is None:
                entry.future.result().close()
        finally:
            # Break the completion callback's reference back to its Future.
            entry.future = entry.code = None
