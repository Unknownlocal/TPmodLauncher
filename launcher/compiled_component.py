# SPDX-License-Identifier: MPL-2.0
"""Create native code from portable Wasm, without a Store or guest execution."""

from contextlib import ExitStack

from wasmtime import Config, Engine
from wasmtime.component import Component

from launcher.load_timing import measure

# These are the actual compiler settings as well as part of the reuse key.
# Cache locations affect loading speed, not the meaning of the compiled code.
COMPILER_SETTINGS = (
    ("wasm_component_model", True),
    ("consume_fuel", True),
    ("epoch_interruption", True),
    # Keep memory accounting simple: no guest threads, shared memory or GC heap.
    ("wasm_threads", False),
    ("shared_memory", False),
    ("wasm_memory64", False),
    ("wasm_multi_memory", False),
    ("wasm_gc", False),
    ("wasm_function_references", False),
    ("max_wasm_stack", 512 * 1024),
    # Native compiler workers are independent of guest execution threads.
    ("parallel_compilation", True),
    # Prefer shorter cold compilation for the bundled Python interpreter.
    ("cranelift_opt_level", "none"),
)


class CompiledComponent:
    def __init__(self, data, timings=None, *, cache=None):
        self.resources = ExitStack()
        self.cache_status = None
        try:
            with Config() as config:
                for name, value in COMPILER_SETTINGS:
                    setattr(config, name, value)
                if cache is not None:
                    self.cache_status = cache.configure(config)
                self.engine = self.resources.enter_context(Engine(config))
            # Validate portable input through Wasmtime; never deserialize mod-supplied native code.
            with measure(timings, "compile"):
                self.component = self.resources.enter_context(Component(self.engine, data))
        except BaseException:
            self.close()
            raise

    def close(self):
        """Release code that has not been transferred to an execution owner."""
        self.resources.close()
