# SPDX-License-Identifier: MPL-2.0
"""Adoption chooses the execution thread's code, not the preparation worker's."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

from wasmtime import wat2wasm

from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Sandbox


def test_background_preparation_joins_only_the_adopting_threads_runtime():
    data = bytes(wat2wasm("(component)"))
    copy = memoryview(data).tobytes()
    with Sandbox(data) as first, ThreadPoolExecutor(max_workers=1) as worker:
        with closing(worker.submit(CompiledMod, copy).result(5)) as prepared:
            discarded_engine = prepared.engine
            with Sandbox(copy, prepared=prepared) as second:
                assert first._engine is second._engine
                assert second._engine is not discarded_engine
                assert prepared.data is None

        def other_execution_thread():
            with Sandbox(data) as third:
                return third._engine

        assert worker.submit(other_execution_thread).result(5) is not first._engine
