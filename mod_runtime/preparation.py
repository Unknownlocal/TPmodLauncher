# SPDX-License-Identifier: MPL-2.0
"""Prepare inert source and trusted runner code without entering a guest."""

from concurrent.futures import CancelledError
from pathlib import Path
import threading

from launcher.compiled_mod import CompiledMod
from launcher.load_timing import measure
from .bundle import RunnerBundle
from .prepared_source import PreparedSource
from .source import SourceError, SourceSnapshot


def _prepare(bundle, folder, cancelled, expected_manifest=None):
    def checkpoint():
        if cancelled.is_set():
            raise CancelledError()

    timings = {}
    checkpoint()
    if type(bundle) is not RunnerBundle:
        raise ValueError("Expected a verified runner bundle")
    with measure(timings, "source_snapshot"):
        snapshot = SourceSnapshot.read(folder)
    checkpoint()
    if expected_manifest is not None and snapshot.manifest != expected_manifest:
        raise SourceError("Source manifest changed after admission")
    with measure(timings, "runner_read"):
        data = bundle.component()
    checkpoint()
    return PreparedSource(snapshot, data, timings)


def _discard(future):
    # Inspect failures without reraising worker BaseExceptions on the controller.
    if not future.cancelled() and future.exception() is None:
        future.result().close()


class SourcePreparation:
    """Read on a worker, then request shared code from the execution thread.

    The session owns the executor and bounds outstanding work. Workers create no
    Store and execute no mod code. Cancellation is checked between bounded read
    stages. take() polls both stages without waiting. The registry disposes an
    abandoned compiler result after it returns, while other users keep theirs.
    Owners cancel each request before executor shutdown; queued shared compiler
    work may still be serving another loading scope and must be allowed to drain.
    """

    def __init__(self, executor, bundle, folder, *, cache=None, expected_manifest=None):
        self._thread = threading.get_ident()
        self._cancelled = threading.Event()
        self._closed = False
        self._result = None
        self._executor, self._cache = executor, cache
        # Bind relative paths now without resolving away links checked by the reader.
        folder = Path(folder).absolute()
        self._future = executor.submit(_prepare, bundle, folder, self._cancelled, expected_manifest)

    def _check_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use source preparation on its creating thread")

    def take(self):
        """Return None while pending, or transfer the completed result exactly once."""
        self._check_thread()
        if self._closed:
            raise RuntimeError("Source preparation is closed or already taken")
        try:
            if self._future is not None:
                if not self._future.done():
                    return None
                self._result = self._future.result()
                self._future = None
                # Resolve sharing here, on the future Store's thread. The worker
                # only read inert source/bytes, so it cannot pick the wrong owner.
                self._result.compiled = CompiledMod(self._result.component, self._result.timings,
                                                    cache=self._cache, executor=self._executor)
                self._result.component = None
            if not self._result.compiled.poll():
                return None
        except BaseException:
            self.close()
            raise
        result, self._result = self._result, None
        self._closed = True
        return result

    def close(self):
        """Abandon this job without waiting; dispose ready and late results."""
        self._check_thread()
        self._closed = True
        if self._future is not None:
            self._cancelled.set()
            self._future.cancel()
            self._future.add_done_callback(_discard)
            self._future = None
        if self._result is not None:
            self._result.close()
            self._result = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
