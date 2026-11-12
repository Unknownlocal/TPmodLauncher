# SPDX-License-Identifier: MPL-2.0
"""Prepare inert source and trusted runner code without entering a guest."""

from concurrent.futures import CancelledError
from pathlib import Path
import threading

from launcher.compiled_mod import CompiledMod
from launcher.load_timing import measure
from .bundle import RunnerBundle
from .prepared_sources import PreparedSources
from .source import SourceError, SourceManifest, SourceSnapshot
from ._source_policy import MAX_GROUP_SOURCE_BYTES, MAX_GROUP_SOURCE_FILES, MAX_SOURCE_OWNERS


def _checkpoint(cancelled):
    if cancelled.is_set():
        raise CancelledError()


def _snapshot(folder, expected_manifest, cancelled):
    """Keep the manifest that was checked before loading, including its permissions."""
    _checkpoint(cancelled)
    snapshot = SourceSnapshot.read(folder)
    _checkpoint(cancelled)
    if snapshot.manifest != expected_manifest:
        raise SourceError("Source manifest changed after admission")
    return snapshot


def _prepare_group(cancelled, bundle, sources):
    """Copy every member before requesting code; peer imports need the full group."""
    _checkpoint(cancelled)
    if type(bundle) is not RunnerBundle:
        raise ValueError("Expected a verified runner bundle")
    snapshots, timings = [], {}
    size = files = 0
    with measure(timings, "source_snapshot"):
        for folder, manifest in sources:
            snapshot = _snapshot(folder, manifest, cancelled)
            # These counts come from the checked disk reader. Stop retaining
            # members as soon as the group is too large, before requesting code.
            size += snapshot.total_bytes
            files += len(snapshot.files)
            if size > MAX_GROUP_SOURCE_BYTES or files > MAX_GROUP_SOURCE_FILES:
                raise SourceError("Shared source group exceeds its budget")
            snapshots.append(snapshot)
    with measure(timings, "runner_read"):
        data = bundle.component()
    _checkpoint(cancelled)
    return PreparedSources(tuple(snapshots), data, timings)


def _discard(future):
    # Inspect failures without reraising worker BaseExceptions on the controller.
    if not future.cancelled() and future.exception() is None:
        future.result().close()


class SourcePreparation:
    """Read a fixed source group, then request code on the execution thread.

    The session owns the executor and bounds outstanding work. Workers create no
    Store and execute no mod code. Cancellation is checked between bounded read
    stages. take() checks ready stages without waiting; completion callbacks
    schedule later calls on the owner. The registry disposes an
    abandoned compiler result after it returns, while other users keep theirs.
    Owners cancel each request before executor shutdown; queued shared compiler
    work may still be serving another loading scope and must be allowed to drain.
    """

    def __init__(self, executor, bundle, sources, *, cache=None, wake=None):
        """Prepare an ordered tuple of (folder, previously checked SourceManifest) pairs.

        Every member must pass: returning a smaller group could break imports or
        silently omit required rules. No source executes here. The returned job
        can be checked with take() or abandoned with close() without waiting.
        wake only queues owner-thread work; it may run immediately or on a worker.
        """
        if type(sources) is not tuple or not 0 < len(sources) <= MAX_SOURCE_OWNERS:
            raise SourceError("Expected a bounded tuple of source group members")
        members, owners = [], set()
        for folder, manifest in sources:
            if type(manifest) is not SourceManifest:
                raise SourceError("Expected an admitted source manifest")
            if manifest.id in owners:
                raise SourceError("Duplicate shared source owner")
            owners.add(manifest.id)
            members.append((Path(folder).absolute(), manifest))
        # Bind relative paths before submission, retaining links for the reader's
        # checks. Workers return text/bytes; take() requests code on the Store thread.
        self._thread = threading.get_ident()
        self._cancelled = threading.Event()
        self._closed = False
        self._result = None
        self._executor, self._cache = executor, cache
        self._wake = wake
        self._future = executor.submit(_prepare_group, self._cancelled, bundle, tuple(members))
        if wake is not None:
            self._future.add_done_callback(lambda _: wake())

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
                if self._wake is not None:
                    self._result.compiled.when_ready(self._wake)
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
