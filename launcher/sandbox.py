# SPDX-License-Identifier: MPL-2.0
"""Run one Component outside the game with only explicitly granted host functions.

This is an internal host interface. The author-facing WIT contract and pack
discovery are separate steps; this module never imports mod Python or links WASI.
"""

from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
import math
import threading
import time

from wasmtime import Config, Engine, Store, WasmtimeError
from wasmtime.component import (
    Bool,
    Component,
    FuncType,
    Linker,
    S32,
    S64,
    String,
    U32,
    U64,
)


class SandboxError(RuntimeError):
    """A component, call or host operation could not run within the allowed boundary."""


@dataclass(frozen=True)
class Limits:
    """Resource budgets chosen by the launcher, not by mod manifests.

    Fuel measures WebAssembly execution work: most Wasm instructions cost one
    unit, while some cost zero. It includes a guest Python interpreter's work;
    it does not count Python statements, native CPU instructions or seconds.
    Running out stops execution and closes the affected sandbox.

    Each exported call gets a fresh fuel budget, shared with its post-return
    cleanup. Initialization uses initialization_fuel, or fuel when it is None.
    Likewise, initialization_seconds overrides seconds only during startup.
    Trusted host callbacks do not spend guest fuel; seconds and host_calls
    separately limit elapsed time and the number of calls into the host.
    """

    component_bytes: int = 32 * 1024 * 1024
    memory_bytes: int = 64 * 1024 * 1024
    fuel: int = 5_000_000  # Fresh Wasm work budget for each exported call.
    seconds: float = 1.0
    host_calls: int = 64
    instances: int = 32
    initialization_fuel: int | None = None  # Optional startup budget; None reuses fuel.
    initialization_seconds: float | None = None  # Optional startup deadline; None reuses seconds.

    def __post_init__(self):
        # A zero, negative or boolean budget would disable or confuse a limit.
        for name in (
            "component_bytes",
            "memory_bytes",
            "fuel",
            "host_calls",
            "instances",
            "initialization_fuel",
        ):
            if name == "initialization_fuel" and self.initialization_fuel is None:
                continue
            if (
                type(getattr(self, name)) is not int
                or not 0 < getattr(self, name) < 2**63
            ):
                raise ValueError(f"{name} must be a positive integer below 2**63")
        for name in ("seconds", "initialization_seconds"):
            value = getattr(self, name)
            if name == "initialization_seconds" and value is None:
                continue
            if (
                type(value) not in (int, float)
                or not math.isfinite(value)
                or value <= 0
                or value > threading.TIMEOUT_MAX
            ):
                raise ValueError(
                    f"{name} must be positive and within the threading timer limit"
                )


# Restrict this first host to scalar WIT values; no resources or host objects cross it.
_TYPES = {
    "bool": Bool,
    "s32": S32,
    "u32": U32,
    "s64": S64,
    "u64": U64,
    "string": String,
}
# Wasmtime's Python callback exception bookkeeping is process-wide. Do not overlap guest entries.
_ENTRY_LOCK = threading.Lock()


@dataclass(frozen=True)
class Operation:
    """A trusted, bounded callback and its expected WIT parameter/result types."""

    parameters: tuple[str, ...]
    result: str | None
    callback: Callable

    def __post_init__(self):
        # Copying is unnecessary when the descriptor contains an immutable tuple.
        if (
            type(self.parameters) is not tuple
            or len(self.parameters) > 8
            or any(kind not in _TYPES for kind in self.parameters)
        ):
            raise ValueError("Expected up to eight supported scalar parameter types")
        if self.result is not None and self.result not in _TYPES:
            raise ValueError("Unsupported result type")
        if not callable(self.callback):
            raise ValueError("Host operation must be callable")


def _signature(function):
    """Read a Component function's scalar signature, rejecting compound values and handles."""

    def kind(value):
        if value is None:
            return None
        for name, value_type in _TYPES.items():
            if type(value) is value_type:
                return name
        raise SandboxError("Unsupported Component value type")

    parameters = tuple(kind(value) for _, value in function.params)
    if len(parameters) > 8:
        raise SandboxError("Too many Component function parameters")
    return parameters, kind(function.result)


def _value(kind, value):
    """Check one scalar or void result without accepting host objects or coercing types."""
    if kind is None and value is None:
        return
    if kind == "bool" and type(value) is bool:
        return
    if (
        kind == "string"
        and type(value) is str
        and len(value) <= 4096
        and len(value.encode("utf-8")) <= 4096
    ):
        return
    if kind in ("s32", "u32", "s64", "u64") and type(value) is int:
        bits = int(kind[1:])
        low, high = (
            (-(2 ** (bits - 1)), 2 ** (bits - 1)) if kind[0] == "s" else (0, 2**bits)
        )
        if low <= value < high:
            return
    raise SandboxError("Expected a bounded plain scalar value")


def _values(kinds, values):
    """Check argument count and values before crossing a host/guest function boundary."""
    if len(kinds) != len(values):
        raise SandboxError("Wrong number of function arguments")
    for kind, value in zip(kinds, values):
        _value(kind, value)


class Sandbox:
    """Own one component, store, capability table and cleanup scope on its creating thread."""

    def __init__(
        self,
        component: bytes,
        capabilities=None,
        *,
        limits=Limits(),
        cleanup=None,
        required_exports=None,
    ):
        # Only trusted infrastructure supplies grants, limits and cleanup; the guest supplies bytes.
        self._thread = threading.get_ident()
        self._busy = False
        self._closed = False
        self.failure = None
        self._resources = ExitStack()
        self._capabilities = dict(capabilities or {})
        self._exports = {}
        self._instance = None
        self._limits = limits
        if cleanup is not None:
            self._resources.callback(cleanup)
        try:
            # Accept validated portable Component binaries, never text, files or serialized native code.
            if (
                type(component) is not bytes
                or not component.startswith(b"\0asm\x0d\0\x01\0")
                or len(component) > limits.component_bytes
            ):
                raise SandboxError(
                    "Expected a size-limited WebAssembly Component binary"
                )
            if len(self._capabilities) > 32 or any(
                type(name) is not str
                or not name
                or len(name) > 128
                or type(operation) is not Operation
                for name, operation in self._capabilities.items()
            ):
                raise SandboxError("Expected at most 32 named host operations")
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
                config.parallel_compilation = False
                # Python components contain a runtime; fast compilation keeps admission responsive.
                config.cranelift_opt_level = "none"
                self._engine = self._resources.enter_context(Engine(config))
            # Compile portable bytes through Wasmtime's validator, not unsafe native deserialization.
            compiled = self._resources.enter_context(Component(self._engine, component))
            with compiled.type as interface:
                imports = interface.imports(self._engine)
                exports = interface.exports(self._engine)
                if len(imports) > 32 or len(exports) > 64:
                    raise SandboxError("Too many Component imports or exports")
                for name, item in imports.items():
                    function = item.ty
                    operation = self._capabilities.get(name)
                    if (
                        operation is None
                        or type(function) is not FuncType
                        or _signature(function)
                        != (operation.parameters, operation.result)
                    ):
                        raise SandboxError(
                            "Component import is not an approved host operation"
                        )
                for name, item in exports.items():
                    function = item.ty
                    if type(function) is not FuncType:
                        # Python components include private binding metadata; it grants no host access.
                        # Only flat scalar functions enter our callable export table.
                        continue
                    self._exports[name] = _signature(function)
                if required_exports is not None and self._exports != required_exports:
                    raise SandboxError(
                        "Component exports do not match the required API"
                    )
            linker = self._resources.enter_context(Linker(self._engine))
            self._store = self._resources.enter_context(Store(self._engine))
            # With one memory allowed, memory_bytes is also the aggregate linear-memory bound.
            self._store.set_limits(
                memory_size=limits.memory_bytes,
                memories=1,
                instances=limits.instances,
                tables=4,
                table_elements=10_000,
            )
            with linker.root() as root:
                for name, operation in self._capabilities.items():
                    root.add_func(name, self._host_function(operation))
            # Start functions execute here, using the startup fuel budget and other execution limits.
            self._instance = self._run(
                lambda: linker.instantiate(self._store, compiled), initialize=True
            )
        except BaseException as error:
            self.close()
            if not isinstance(error, Exception):
                raise
            raise SandboxError(str(error)[:512]) from None

    @property
    def closed(self):
        """Report whether the instance has released its native resources and grants."""
        return self._closed

    def _host_function(self, operation):
        """Wrap a trusted operation with this instance's call budget and value checks."""

        def invoke(_store, *arguments):
            try:
                self._calls += 1
                if (
                    self._calls > self._limits.host_calls
                    or time.monotonic() >= self._deadline
                ):
                    raise SandboxError("Host operation budget exceeded")
                _values(operation.parameters, arguments)
                result = operation.callback(*arguments)
                _value(operation.result, result)
                # Epochs cannot preempt trusted Python work; reject a late return before continuing Wasm.
                if time.monotonic() >= self._deadline:
                    raise SandboxError("Host operation exceeded its deadline")
                return result
            except BaseException as error:
                # Return a native error through Wasmtime's callback trampoline, never a Python exception.
                # This also avoids leaving its global pending-exception slot set after instantiation.
                self.failure = str(error)[:512]
                raise WasmtimeError(self.failure) from None

        return invoke

    def _check_entry(self):
        """Reject closed, concurrent and nested calls before touching the native store."""
        if threading.get_ident() != self._thread:
            raise SandboxError("Use the sandbox on its creating thread")
        if self._closed:
            raise SandboxError("Sandbox is closed")
        if self._busy:
            self.failure = "Nested sandbox entry is unsupported"
            raise SandboxError(self.failure)

    def _run(self, action, *, initialize=False):
        """Bound one initialization/call, including host callbacks and post-return work."""
        self._check_entry()
        if not _ENTRY_LOCK.acquire(blocking=False):
            raise SandboxError("Nested or concurrent sandbox entry is unsupported")
        self._busy = True
        self._calls = 0
        # Startup can have a longer allowance without extending later callbacks.
        seconds = self._limits.initialization_seconds if initialize else None
        seconds = seconds or self._limits.seconds
        self._deadline = time.monotonic() + seconds
        timer = threading.Timer(seconds, self._engine.increment_epoch)
        timer.daemon = True
        try:
            # Reset guest work for this entry; unused fuel does not carry over to the next call.
            fuel = self._limits.initialization_fuel if initialize else None
            self._store.set_fuel(fuel or self._limits.fuel)
            self._store.set_epoch_deadline(1)
            timer.start()
            result = action()
            if self.failure is not None or time.monotonic() >= self._deadline:
                raise SandboxError(
                    self.failure or "Guest execution exceeded its deadline"
                )
            return result
        except BaseException as error:
            # Keep diagnostics bounded; the original exception may contain guest-generated text.
            self.failure = self.failure or str(error)[:512]
            if not isinstance(error, Exception):
                raise
            raise SandboxError(self.failure) from None
        finally:
            # Join before freeing Engine so the timer can never increment a closed native handle.
            timer.cancel()
            if timer.ident is not None:
                timer.join()
            self._busy = False
            _ENTRY_LOCK.release()
            if self.failure is not None:
                self.close()

    def call(self, name, *arguments):
        """Invoke a scalar export; any invalid request, trap or timeout disables this instance."""

        def invoke():
            if type(name) is not str or name not in self._exports:
                raise SandboxError("Unknown Component function")
            parameters, result_type = self._exports[name]
            _values(parameters, arguments)
            function = self._instance.get_func(self._store, name)
            result = function(self._store, *arguments)
            _value(result_type, result)
            # Post-return may itself execute guest code; it shares the original fuel/deadline.
            function.post_return(self._store)
            return result

        return self._run(invoke)

    def close(self):
        """Revoke grants, release native state and run trusted cleanup exactly once."""
        if self._closed:
            return
        self._check_entry()
        self._closed = True
        self._instance = None
        self._exports.clear()
        self._capabilities.clear()
        try:
            self._resources.close()
        except Exception as error:
            self.failure = self.failure or str(error)[:512]
            raise SandboxError(self.failure) from None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
