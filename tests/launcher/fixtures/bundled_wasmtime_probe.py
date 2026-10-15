# SPDX-License-Identifier: MPL-2.0
"""Run an owned Component through the real bundled Python bindings and native DLL."""

import importlib.util
from pathlib import Path

root = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "bundled_dependencies", root / "launcher/bundled_dependencies.py"
)
dependencies = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dependencies)
dependencies.import_dependencies(root)

# Only trusted test code creates an engine here; production mod hosting comes later.
from wasmtime import Engine, Store
from wasmtime.component import Component, Linker

with Engine() as engine, Store(engine) as store, Linker(engine) as linker:
    with Component(
        engine,
        """
        (component
          (core module $module
            (func (export "answer") (result i32) i32.const 42))
          (core instance $instance (instantiate $module))
          (func (export "answer") (result u32)
            (canon lift (core func $instance "answer"))))
    """,
    ) as component:
        instance = linker.instantiate(store, component)
        answer = instance.get_func(store, "answer")
        assert answer(store) == 42
        answer.post_return(store)
print("Component returned 42")
