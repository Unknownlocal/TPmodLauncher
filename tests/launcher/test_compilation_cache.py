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


def test_cache_never_replaces_current_manifest_validation(
    tmp_path, code_mod
):
    folder = tmp_path / "data/pack"
    mod = code_mod(folder, "owned.cached")
    pack = dict(state="ready", series="formula", id="pack", root=str(folder.resolve()))
    cache, messages = CompilationCache(tmp_path), []
    for attempt in range(2):
        if attempt:
            (mod / "mod.toml").write_text("invalid manifest")
        with closing(
            PackMods(tmp_path, messages.append, cache=cache, background=True)
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


@pytest.fixture
def darwin_acl_api(tmp_path, monkeypatch):
    from launcher import compilation_cache

    opened, closed, freed = [], [], []

    def open_directory(path, flags):
        assert path == tmp_path
        assert flags == 0x100100
        opened.append(path)
        return 42

    def get_acl(descriptor, kind):
        assert descriptor == 42 and kind == 0x100
        return 123

    api = SimpleNamespace(
        acl_get_fd_np=get_acl,
        acl_get_entry=lambda *_: 0,
        acl_free=lambda acl: freed.append(acl),
    )
    monkeypatch.setattr(ctypes, "CDLL", lambda *_args, **_kwargs: api)
    # Windows cannot open directories this way. Replace only the OS boundary;
    # native result/errno handling and cleanup still run in the real adapter.
    monkeypatch.setattr(
        compilation_cache,
        "os",
        SimpleNamespace(
            open=open_directory,
            close=closed.append,
            O_RDONLY=0,
            O_DIRECTORY=0x100000,
            O_NOFOLLOW=0x100,
        ),
    )
    return api, opened, closed, freed


@pytest.mark.parametrize(
    "entry_result,error", [(-1, errno.EINVAL), (0, 0), (-1, errno.EACCES)]
)
def test_darwin_acl_adapter_distinguishes_empty_acl_from_entries_and_errors(
    tmp_path, darwin_acl_api, entry_result, error
):
    from launcher import compilation_cache

    api, opened, closed, freed = darwin_acl_api

    def get_entry(*_):
        ctypes.set_errno(error)
        return entry_result

    api.acl_get_entry = get_entry
    if entry_result == -1 and error == errno.EINVAL:
        compilation_cache._macos_private_acl(tmp_path)
    else:
        with pytest.raises((ValueError, OSError)):
            compilation_cache._macos_private_acl(tmp_path)
    assert opened == [tmp_path] and closed == [42]
    assert freed == [123]


@pytest.mark.parametrize(
    "error", [errno.ENOENT, errno.EACCES, 0]
)
def test_darwin_acl_adapter_distinguishes_absent_acl_from_read_errors(
    tmp_path, darwin_acl_api, error
):
    from launcher import compilation_cache

    api, opened, closed, freed = darwin_acl_api

    def get_acl(*_):
        # No errno must not accidentally reuse an ENOENT from an earlier call.
        assert ctypes.get_errno() == 0
        ctypes.set_errno(error)
        return None

    def unexpected_entry(*_):
        pytest.fail("A null ACL must not be inspected")

    api.acl_get_fd_np = get_acl
    api.acl_get_entry = unexpected_entry
    ctypes.set_errno(errno.ENOENT)
    if error == errno.ENOENT:
        compilation_cache._macos_private_acl(tmp_path)
    else:
        with pytest.raises(OSError, match="Cannot read cache access rules") as caught:
            compilation_cache._macos_private_acl(tmp_path)
        assert caught.value.errno == error
    assert opened == [tmp_path] and closed == [42]
    assert freed == []


def test_darwin_acl_adapter_rejects_a_missing_directory(
    tmp_path, darwin_acl_api, monkeypatch
):
    from launcher import compilation_cache

    api, opened, closed, freed = darwin_acl_api

    def missing_directory(*_):
        raise FileNotFoundError(errno.ENOENT, "Directory disappeared")

    def unexpected_acl(*_):
        pytest.fail("A missing directory must not reach the ACL probe")

    monkeypatch.setattr(compilation_cache.os, "open", missing_directory)
    api.acl_get_fd_np = unexpected_acl
    with pytest.raises(FileNotFoundError, match="Directory disappeared"):
        compilation_cache._macos_private_acl(tmp_path)
    assert opened == closed == freed == []


@pytest.mark.skipif(sys.platform != "darwin", reason="Requires real macOS ACLs")
def test_darwin_acl_probe_accepts_no_acl_and_rejects_extended_entries(tmp_path):
    import pwd

    from launcher import compilation_cache

    folder = tmp_path / "private cache"
    folder.mkdir(mode=0o700)
    compilation_cache._private_directory(folder)
    user = pwd.getpwuid(os.geteuid()).pw_name
    subprocess.run(
        ["/bin/chmod", "+a", f"user:{user} allow list,search", str(folder)],
        check=True,
        capture_output=True,
        text=True,
    )
    with pytest.raises(ValueError, match="Extended cache access rules"):
        compilation_cache._private_directory(folder)
