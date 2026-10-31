# SPDX-License-Identifier: MPL-2.0
"""Ready/pending reuse must avoid native copies without coupling cancellation."""

from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import closing
import threading

import pytest
from wasmtime import WasmtimeError, wat2wasm

from launcher import compilation_registry
from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Sandbox


@pytest.fixture
def compiler(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls, results = [], []
    original = compilation_registry.CompiledComponent

    def compile(*args, **kwargs):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(10), "Test did not release compiler"
        result = original(*args, **kwargs)
        results.append(result)
        return result

    monkeypatch.setattr(compilation_registry, "CompiledComponent", compile)
    with ThreadPoolExecutor(max_workers=1) as executor:
        try:
            yield executor, entered, release, calls, results
        finally:
            release.set()


def test_pending_and_ready_requests_compile_once_and_cancel_independently(compiler):
    executor, entered, release, calls, results = compiler
    data = bytes(wat2wasm("(component)"))
    copy = memoryview(data).tobytes()
    with closing(CompiledMod(data, executor=executor)) as first, closing(CompiledMod(copy, executor=executor)) as second:
        assert entered.wait(5)
        future = first._entry.future
        assert second._entry.future is future
        assert not first.poll() and not second.poll()
        first.close()
        assert not future.cancelled()
        release.set()
        assert wait([future], timeout=5).done
        with Sandbox(copy, prepared=second) as sandbox, closing(CompiledMod(data, executor=executor)) as ready:
            assert ready.poll() and ready.cache_status["state"] == "reused"
            with Sandbox(data, prepared=ready) as peer:
                assert sandbox._engine is peer._engine
        assert len(calls) == 1 and calls[0] != threading.get_ident()
    with pytest.raises(ValueError, match="closed"):
        results[0].engine.ptr()


def test_abandoned_running_compilation_can_be_rejoined_and_is_freed_after_drain(compiler):
    executor, entered, release, calls, results = compiler
    data = bytes(wat2wasm("(component)"))
    with closing(CompiledMod(data, executor=executor)) as first:
        assert entered.wait(5)
        future = first._entry.future
        first.close()
        assert future.running()
        with closing(CompiledMod(data, executor=executor)) as second:
            assert second._entry.future is future
        release.set()
        # A following job runs after the compiler's completion/cleanup callback.
        executor.submit(lambda: None).result(5)
    with pytest.raises(ValueError, match="closed"):
        results[0].engine.ptr()
    with Sandbox(data):
        assert len(calls) == 2  # No abandoned result is kept as a permanent cache.


def test_only_last_request_cancels_a_queued_compilation(compiler):
    executor, entered, release, calls, _ = compiler
    occupied, unblock = threading.Event(), threading.Event()

    def occupy():
        occupied.set()
        assert unblock.wait(10)

    executor.submit(occupy)
    assert occupied.wait(5)
    data = bytes(wat2wasm("(component)"))
    try:
        with closing(CompiledMod(data, executor=executor)) as first, closing(CompiledMod(data, executor=executor)) as second:
            future = first._entry.future
            first.close()
            assert not future.cancelled()
            second.close()
            assert future.cancelled() and not entered.is_set()
    finally:
        unblock.set()
        release.set()
    with Sandbox(data):
        assert len(calls) == 1


def test_shared_failure_reaches_each_request_and_does_not_prevent_retry(compiler):
    executor, entered, release, calls, _ = compiler
    data = b"\0asm\x0d\0\x01\0\xff"
    with closing(CompiledMod(data, executor=executor)) as first, closing(CompiledMod(data, executor=executor)) as second:
        assert entered.wait(5)
        future = first._entry.future
        release.set()
        assert wait([future], timeout=5).done
        for request in (first, second):
            with pytest.raises(WasmtimeError):
                request.poll()
    with pytest.raises(WasmtimeError):
        CompiledMod(data)
    assert len(calls) == 2
