# SPDX-License-Identifier: MPL-2.0
"""Load an owned cached component in a fresh Python process."""

from contextlib import closing
import json
from pathlib import Path
import sys

sys.path.insert(0, sys.argv[1])
from launcher.compilation_cache import CompilationCache
from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Operation, Sandbox

component = Path(sys.argv[3]).read_bytes()
with closing(CompiledMod(component, cache=CompilationCache(sys.argv[2]))) as prepared:
    with Sandbox(
        component,
        {"bonus": Operation(("s32",), "s32", lambda value: value)},
        prepared=prepared,
    ) as guest:
        print(
            json.dumps(
                {
                    "cache": prepared.cache_status["state"],
                    "counter": guest.call("counter"),
                }
            )
        )
