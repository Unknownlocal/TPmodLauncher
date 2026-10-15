# SPDX-License-Identifier: MPL-2.0
"""Check real Wasmtime caching, fresh instances and cold fallback for unsafe paths."""

from contextlib import closing
from concurrent.futures import wait
import ctypes
import errno
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from wasmtime import Config, wat2wasm

from launcher.compilation_cache import CompilationCache
from launcher.compiled_mod import CompiledMod
from launcher.pack_mods import PackMods
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


def test_changed_compiler_settings_get_distinct_native_cache_entries(
    tmp_path, cache_guest, monkeypatch
):
    from launcher import compiled_mod

    cache = CompilationCache(tmp_path)
    first = CompiledMod(cache_guest, cache=cache)
    first.close()
    artifacts = tmp_path / "bin/wasmtime-cache/artifacts"
    before = {
        p
        for p in artifacts.rglob("*")
        if p.is_file() and not p.suffix and not p.name.startswith(".")
    }
    assert before

    class SpeedConfig(Config):
        # Use the real native configuration with just one changed compilation setting.
        def __setattr__(self, name, value):
            super().__setattr__(
                name, "speed" if name == "cranelift_opt_level" else value
            )

    monkeypatch.setattr(compiled_mod, "Config", SpeedConfig)
    with closing(CompiledMod(cache_guest, cache=cache)) as second:
        assert second.cache_status["state"] == "enabled"
        with Sandbox(
            cache_guest,
            {"bonus": Operation(("s32",), "s32", lambda value: value)},
            prepared=second,
        ) as guest:
            assert guest.call("counter") == 1
    after = {
        p
        for p in artifacts.rglob("*")
        if p.is_file() and not p.suffix and not p.name.startswith(".")
    }
    assert after - before


@pytest.mark.parametrize(
    "obstacle",
    ["parent_file", "inherited_access", "hardlink", "fifo", "unsupported_acl"],
)
def test_unusable_cache_falls_back_without_executing_its_files(
    tmp_path, cache_guest, obstacle, monkeypatch
):
    if obstacle == "fifo" and os.name == "nt":
        pytest.skip("Windows has no Unix FIFO files")
    if obstacle == "unsupported_acl" and os.name != "nt":
        pytest.skip("The Windows ACL probe is not used on Unix")
    parent = tmp_path / "bin"
    if obstacle == "parent_file":
        parent.write_text("owned blocking file")
    else:
        parent.mkdir()
        folder = parent / "wasmtime-cache"
        folder.mkdir(mode=0o777 if obstacle == "inherited_access" else 0o700)
        if obstacle == "hardlink":
            outside = tmp_path / "outside"
            outside.write_text("never execute")
            os.link(outside, folder / "linked-native-cache")
        elif obstacle == "fifo":
            os.mkfifo(folder / "not-a-cache-file")
        elif obstacle == "unsupported_acl":
            from launcher import compilation_cache

            # Simulate a filesystem returning unrestricted rules for both private-directory probes.
            monkeypatch.setattr(
                compilation_cache,
                "_windows_security",
                lambda _: "O:BUD:NO_ACCESS_CONTROL",
            )
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


@pytest.mark.parametrize(
    "link_at", ["bin", "bin/wasmtime-cache", "bin/wasmtime-cache/artifacts"]
)
def test_cache_links_are_not_followed_or_repaired(tmp_path, cache_guest, link_at):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("untouched")
    link = tmp_path / link_at
    link.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
            check=True,
            capture_output=True,
        )
    else:
        link.symlink_to(outside, target_is_directory=True)
    try:
        with closing(
            CompiledMod(cache_guest, cache=CompilationCache(tmp_path))
        ) as prepared:
            assert prepared.cache_status["state"] == "disabled"
        assert (
            list(outside.iterdir()) == [sentinel]
            and sentinel.read_text() == "untouched"
        )
    finally:
        link.rmdir() if os.name == "nt" else link.unlink()


@pytest.mark.parametrize("background", [False, True])
def test_cache_never_replaces_current_manifest_validation(
    tmp_path, code_mod, background
):
    folder = tmp_path / "data/pack"
    mod = code_mod(folder, "owned.cached")
    pack = dict(state="ready", series="formula", id="pack", root=str(folder.resolve()))
    cache, messages = CompilationCache(tmp_path), []
    for attempt in range(2):
        if attempt:
            (mod / "mod.toml").write_text("invalid manifest")
        with closing(
            PackMods(tmp_path, messages.append, cache=cache, background=background)
        ) as scope:
            scope.load(pack)
            while scope.loading is not None:
                if scope.pending is not None:
                    assert wait([scope.pending], timeout=10).done
                scope.poll()
            assert list(scope.mods) == ([] if attempt else ["owned.cached"])
    assert any(
        "CODE MOD CACHE:" in message and '"enabled"' in message for message in messages
    )
    assert any("REJECTED" in message for message in messages)


def test_cache_configuration_cannot_redirect_to_a_mod_supplied_path(
    tmp_path, cache_guest
):
    root = tmp_path / "owned cache \U0001f3ce"
    root.mkdir()
    cache = CompilationCache(root)
    for _ in range(2):
        with closing(CompiledMod(cache_guest, cache=cache)) as prepared:
            assert prepared.cache_status["state"] == "enabled", prepared.cache_status
            assert (
                Path(prepared.cache_status["directory"])
                == root / "bin/wasmtime-cache/artifacts"
            )
        # Even valid TOML in a previous config file is regenerated from trusted policy.
        (root / "bin/wasmtime-cache/config.toml").write_text(
            '[cache]\ndirectory = "/elsewhere"\n'
        )


@pytest.mark.parametrize(
    "entry_result,error", [(-1, errno.EINVAL), (0, 0), (-1, errno.EACCES)]
)
def test_darwin_acl_adapter_distinguishes_empty_acl_from_entries_and_errors(
    tmp_path, monkeypatch, entry_result, error
):
    from launcher import compilation_cache

    freed = []

    def get_entry(*_):
        ctypes.set_errno(error)
        return entry_result

    api = SimpleNamespace(
        acl_get_file=lambda *_: 123,
        acl_get_entry=get_entry,
        acl_free=lambda acl: freed.append(acl),
    )
    # Only the unavailable OS call is replaced; the adapter's native return handling runs normally.
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: api)
    if entry_result == -1 and error == errno.EINVAL:
        compilation_cache._macos_private_acl(tmp_path)
    else:
        with pytest.raises((ValueError, OSError)):
            compilation_cache._macos_private_acl(tmp_path)
    assert freed == [123]
