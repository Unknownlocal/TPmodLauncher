# SPDX-License-Identifier: MPL-2.0
"""Prepare inert source and trusted runner code without entering a guest."""

from concurrent.futures import CancelledError
from dataclasses import dataclass
from pathlib import Path
import threading

from launcher.compiled_mod import CompiledMod
from launcher.load_timing import measure
from .bundle import RunnerBundle
from .source import SourceError, SourceSnapshot


@dataclass
class PreparedSource:
    """Own a worker's result until the controller adopts or discards it."""

    snapshot: SourceSnapshot | None
    compiled: CompiledMod
    timings: dict

    def close(self):
        self.snapshot = None
        self.compiled.close()

    def __enter__(self):
        if self.snapshot is None:
            raise RuntimeError("Source preparation result is closed")
        return self

    def __exit__(self, *_):
        self.close()


def _prepare(bundle, folder, cache, cancelled, expected_manifest=None):
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
    compiled = CompiledMod(data, timings, cache=cache)
    try:
        checkpoint()
        return PreparedSource(snapshot, compiled, timings)
    except BaseException:
        compiled.close()
        raise


def _discard(future):
    # Inspect failures without reraising worker BaseExceptions on the controller.
    if not future.cancelled() and future.exception() is None:
        future.result().close()


class SourcePreparation:
    """One job on a caller-owned thread executor; take/close run on its creating thread.

    The session owns the executor and bounds outstanding work. Workers create no
    Store and execute no mod code. Cancellation is checked between bounded read
    stages; a running native compiler must return before its result is released.
    """

    def __init__(self, executor, bundle, folder, *, cache=None, expected_manifest=None):
        self._thread = threading.get_ident()
        self._cancelled = threading.Event()
        # Bind relative paths now without resolving away links checked by the reader.
        folder = Path(folder).absolute()
        self._future = executor.submit(_prepare, bundle, folder, cache, self._cancelled, expected_manifest)

    def _check_thread(self):
        if threading.get_ident() != self._thread:
            raise RuntimeError("Use source preparation on its creating thread")

    def take(self):
        """Return None while pending, or transfer the completed result exactly once."""
        self._check_thread()
        if self._future is None:
            raise RuntimeError("Source preparation is closed or already taken")
        if not self._future.done():
            return None
        try:
            result = self._future.result()
        except BaseException:
            self.close()
            raise
        self._future = None
        return result

    def close(self):
        """Abandon this job without waiting; dispose ready and late results."""
        self._check_thread()
        if self._future is not None:
            self._cancelled.set()
            self._future.cancel()
            self._future.add_done_callback(_discard)
            self._future = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
