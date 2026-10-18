# SPDX-License-Identifier: MPL-2.0
"""Check real Wasmtime caching, fresh instances and cold fallback for unsafe paths."""

from contextlib import closing
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from wasmtime import wat2wasm

from launcher.compilation_cache import CompilationCache
from launcher.compiled_mod import CompiledMod
from launcher.sandbox import Operation, Sandbox, SandboxError

FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def cache_guest():
    return bytes(wat2wasm((FIXTURES / "sandbox/functions.wat").read_text()))


def test_warm_cache_keeps_fresh_state_and_current_grants(tmp_path, cache_guest):
    cache = CompilationCache(tmp_path)
    for bonus in (6, 20):
        with closing(CompiledMod(cache_guest, cache=cache)) as prepared:
            assert prepared.cache_status["state"] == "enabled", prepared.cache_status
            with Sandbox(
                cache_guest,
                {"bonus": Operation(("s32",), "s32", lambda value: value + bonus)},
                prepared=prepared,
            ) as guest:
                assert guest.call("counter") == 1
                assert guest.call("counter") == 2
                assert guest.call("apply", 2) == 2 + bonus
        assert any((tmp_path / "bin/wasmtime-cache/artifacts").rglob("*"))
    with closing(CompiledMod(cache_guest, cache=cache)) as prepared:
        # Cached machine code cannot restore a permission from the previous instance.
        with pytest.raises(SandboxError, match="approved host operation"):
            Sandbox(cache_guest, {}, prepared=prepared)


def test_cache_reuse_in_fresh_process_and_changed_component(tmp_path, cache_guest):
    guest = tmp_path / "guest.wasm"
    guest.write_bytes(cache_guest)
    script = FIXTURES / "compilation_cache_probe.py"
    command = [
        sys.executable,
        "-B",
        str(script),
        str(Path(__file__).resolve().parents[2]),
        str(tmp_path),
        str(guest),
    ]
    for expected in (1, 1, 3):
        if expected == 3:
            # The same file path now contains different portable code; old native code must not win.
            source = (
                (FIXTURES / "sandbox/functions.wat")
                .read_text()
                .replace(
                    "global.get $counter i32.const 1 i32.add",
                    "global.get $counter i32.const 3 i32.add",
                )
            )
            guest.write_bytes(wat2wasm(source))
        result = subprocess.run(command, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        record = json.loads(result.stdout)
        assert record == {"cache": "enabled", "counter": expected}


def test_hardlinked_cache_falls_back_without_executing_its_files(tmp_path, cache_guest):
    folder = tmp_path / "bin/wasmtime-cache"
    folder.mkdir(parents=True, mode=0o700)
    outside = tmp_path / "outside"
    outside.write_text("never execute")
    os.link(outside, folder / "linked-native-cache")
    with closing(
        CompiledMod(cache_guest, cache=CompilationCache(tmp_path))
    ) as prepared:
        assert prepared.cache_status["state"] == "disabled", prepared.cache_status
        with Sandbox(
            cache_guest,
            {"bonus": Operation(("s32",), "s32", lambda value: value)},
            prepared=prepared,
        ) as guest:
            assert guest.call("counter") == 1
