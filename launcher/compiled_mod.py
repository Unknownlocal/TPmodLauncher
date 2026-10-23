# SPDX-License-Identifier: MPL-2.0
"""Compile portable Wasm without creating a Store or executing guest code."""

from contextlib import ExitStack
from wasmtime import Config, Engine
from wasmtime.component import Component
from launcher.load_timing import measure
from launcher.compiled_code import CompiledCode


class CompiledMod:
    """Own one compiler result until the controller adopts or discards it."""

    def __init__(self, data, timings=None, *, cache=None):
        self.data = data
        self.resources = ExitStack()
        self.closed = False
        self.cache_status = None
        self.code = None
        try:
            if (
                type(data) is not bytes
                or not data.startswith(b"\0asm\x0d\0\x01\0")
                or len(data) > 32 * 1024 * 1024
            ):
                raise ValueError("Expected a size-limited WebAssembly Component binary")
            with Config() as config:
                config.wasm_component_model = True
                config.consume_fuel = True
                config.epoch_interruption = True
                # Keep memory accounting simple; guests receive no threads, shared memory or GC heap.
                config.wasm_threads = False
                config.shared_memory = False
                config.wasm_memory64 = False
                config.wasm_multi_memory = False
                config.wasm_gc = False
                config.wasm_function_references = False
                config.max_wasm_stack = 512 * 1024
                # Compile functions on Wasmtime's worker threads; guest execution stays serialized.
                config.parallel_compilation = True
                # Prefer faster compilation for components that include a Python runtime.
                config.cranelift_opt_level = "none"
                if cache is not None:
                    self.cache_status = cache.configure(config)
                self.engine = self.resources.enter_context(Engine(config))
            # Compile portable bytes through Wasmtime's validator, not unsafe native deserialization.
            with measure(timings, "compile"):
                self.component = self.resources.enter_context(
                    Component(self.engine, data)
                )
        except BaseException:
            self.close()
            raise

    def take(self, data):
        """Adopt code on the Store's thread, with a lease instead of another copy."""
        # Clearing data marks both a transferred result and a closed result as unavailable.
        if self.data is None or data is not self.data:
            raise ValueError("Compiler result is closed, already adopted or mismatched")
        self.code, lease = CompiledCode.adopt(
            data, self.engine, self.component, self.resources
        )
        self.engine, self.component = self.code.engine, self.code.component
        self.data = None
        return lease

    def close(self):
        """Release resources that have not been transferred to a sandbox."""
        if not self.closed:
            self.closed = True
            self.data = None
            self.code = None
            self.resources.close()
