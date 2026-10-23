# SPDX-License-Identifier: MPL-2.0
"""Preparation reuses the execution thread's code before creating a duplicate."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing

import pytest
from wasmtime import wat2wasm

from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Sandbox


def test_background_preparation_joins_only_the_adopting_threads_runtime():
    data = bytes(wat2wasm("(component)"))
    copy = memoryview(data).tobytes()
    with Sandbox(data) as first, ThreadPoolExecutor(max_workers=1) as worker:
        with closing(CompiledMod(copy, executor=worker)) as prepared:
            with pytest.raises(RuntimeError, match="execution thread"):
                worker.submit(prepared.close).result(5)
            with Sandbox(copy, prepared=prepared) as second:
                assert first._engine is second._engine
                assert prepared.engine is first._engine
                assert prepared.data is None
        different = bytes(wat2wasm("(component (core module))"))
        with Sandbox(different) as other_code:
            assert other_code._engine is not first._engine

        def other_execution_thread():
            with Sandbox(data) as third:
                return third._engine

        assert worker.submit(other_execution_thread).result(5) is not first._engine
