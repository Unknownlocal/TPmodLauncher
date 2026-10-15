# SPDX-License-Identifier: MPL-2.0
"""Load selected-pack child mods using real manifests, filesystem paths and Wasmtime."""

from contextlib import closing
from concurrent.futures import wait
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import threading

import pytest
from wasmtime import wat2wasm

from launcher.pack_mods import MAX_MODS, MAX_PACK_ENTRIES, PackMods
from launcher.mod_contract import Manifest, Mod


@pytest.fixture
def pack(tmp_path):
    folder = tmp_path / "data/owned pack"
    folder.mkdir(parents=True)
    record = {
        "state": "ready",
        "series": "formula",
        "id": folder.name,
        "root": str(folder.resolve()),
    }
    return tmp_path, folder, record


def test_only_direct_declared_mods_load_once_in_id_order(pack, code_mod):
    game, folder, record = pack
    # Folder order disagrees with ID order; only IDs determine activation order.
    code_mod(folder, "first-folder", mod_id="owned.z")
    code_mod(folder, "last-folder", mod_id="owned.a")
    code_mod(folder / "container", "owned.nested")
    (folder / "ordinary").mkdir()
    (folder / "ordinary/mod.py").write_text("raise AssertionError('not imported')")
    (folder / "mod.toml").write_text("not a child mod")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load(record)
        assert list(scope.mods) == ["owned.a", "owned.z"]
        instances = list(scope.mods.values())
        assert all(mod.event("owned.event", None) == {} for mod in instances)
        before = list(messages)
        scope.load({**record, "id": "another pack"})
        assert messages == before and list(scope.mods.values()) == instances
    assert all(mod.sandbox.closed for mod in instances)
    closed = [
        json.loads(line.split(": ", 1)[1])["id"]
        for line in messages
        if line.startswith("CODE MOD CLOSED:")
    ]
    assert closed == ["owned.z", "owned.a"]
    before = list(messages)
    scope.close()
    scope.load(record)
    assert not scope.mods and messages == before


def test_duplicate_invalid_missing_and_trapping_mods_do_not_stop_neighbours(
    pack, code_mod
):
    game, folder, record = pack
    code_mod(folder, "duplicate-a", mod_id="owned.duplicate")
    code_mod(folder, "duplicate-b", mod_id="owned.duplicate")
    bad = code_mod(folder, "owned.invalid")
    (bad / "mod.toml").write_text("not valid TOML")
    missing = code_mod(folder, "owned.missing")
    (missing / "mod.wasm").unlink()
    code_mod(folder, "owned.trap", trap=True)
    code_mod(folder, "owned.healthy")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load(record)
        assert list(scope.mods) == ["owned.healthy"]
        assert scope.mods["owned.healthy"].event("echo", None) == {}
        assert sum("Duplicate mod ID" in line for line in messages) == 2
        for rejected in ("owned.invalid", "owned.missing", "owned.trap"):
            assert any("REJECTED" in line and rejected in line for line in messages)


@pytest.mark.parametrize(
    "change",
    [
        {"state": "unavailable"},
        {"series": "oval"},
        {"id": "../escape"},
        {"id": "data/other"},
        {"root": "/unrelated"},
    ],
)
def test_untrusted_pack_records_cannot_choose_another_folder(pack, code_mod, change):
    game, folder, record = pack
    code_mod(folder, "owned.valid")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load({**record, **change})
        assert not scope.mods and "DISCOVERY REJECTED" in messages[0]
        scope.load(record)  # A rejected snapshot is not reassigned later.
        assert not scope.mods and len(messages) == 1


def test_plain_pack_is_valid_and_does_not_import_python(pack):
    game, folder, record = pack
    (folder / "constants.py").write_text("raise AssertionError('never imported')")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load(record)
        assert not scope.mods
        ready = json.loads(messages[-1].split(": ", 1)[1])
        assert ready["active"] == 0
        assert 0 <= ready["phase_seconds"]["discovery"] <= ready["total_seconds"]


@pytest.mark.parametrize(
    "failure", ["component_read", "compile", "initialize", "activate"]
)
@pytest.mark.parametrize("background", [False, True])
def test_loading_timings_survive_failed_phases_and_keep_mod_identity(
    pack, code_mod, failure, background
):
    game, folder, record = pack
    failed = code_mod(folder, "owned.failed", trap=failure == "activate")
    code_mod(folder, "owned.healthy")
    component = failed / "mod.wasm"
    if failure == "component_read":
        component.unlink()
    elif failure == "compile":
        # Pass the Component header check but fail Wasmtime's binary validator.
        component.write_bytes(b"\0asm\x0d\0\x01\0\xff")
    elif failure == "initialize":
        # A valid core start function traps only when the component is instantiated.
        source = (Path(__file__).with_name("fixtures") / "contract.wat").read_text()
        source = source.replace("@RESULT@", "{}").replace("@LENGTH@", "2")
        source = source.replace(
            "(global $heap", "(func $start unreachable) (start $start)\n    (global $heap"
        )
        component.write_bytes(wat2wasm(source))
    messages = []
    progress = []
    with closing(
        PackMods(
            game, messages.append, background=background, set_loading=progress.append
        )
    ) as scope:
        scope.load(record)
        while scope.loading is not None:
            if scope.pending is not None:
                assert wait([scope.pending], timeout=10).done
            scope.poll()
        assert list(scope.mods) == ["owned.healthy"]
        assert progress[-1] == dict(state="complete", loaded=1, failed=1, total=2)
        assert scope.mods["owned.healthy"].event("echo", None) == {}
    timings = {}
    for line in messages:
        if line.startswith("CODE MOD TIMING:"):
            row = json.loads(line.split(": ", 1)[1])
            assert row["pack"] == folder.name
            timings[row["id"]] = row["phase_seconds"]
    phases = ["manifest", "component_read", "compile", "initialize", "activate"]
    assert set(timings["owned.failed"]) == set(phases[: phases.index(failure) + 1])
    assert set(timings["owned.healthy"]) == set(phases)
    for durations in timings.values():
        assert all(math.isfinite(value) and value >= 0 for value in durations.values())
    ready = next(line for line in messages if line.startswith("CODE MOD READY:"))
    assert json.loads(ready.split(": ", 1)[1])["total_seconds"] >= 0


@pytest.mark.parametrize("limit", ["mods", "entries"])
def test_discovery_limits_reject_the_pack_before_any_activation(pack, code_mod, limit):
    game, folder, record = pack
    code_mod(folder, "owned.valid")
    if limit == "mods":
        # Discovery counts declared folders before reading manifests or building instances.
        for number in range(MAX_MODS):
            candidate = folder / f"candidate-{number}"
            candidate.mkdir()
            (candidate / "mod.toml").touch()
    else:
        for number in range(MAX_PACK_ENTRIES):
            (folder / f"ordinary-{number}").touch()
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load(record)
        assert not scope.mods and "DISCOVERY REJECTED" in messages[0]
        assert not any("ACTIVE:" in line for line in messages)


def test_linked_child_and_pack_cannot_redirect_discovery(pack, code_mod):
    game, folder, record = pack
    outside = game / "outside"
    code_mod(outside, "owned.outside")

    def link(path, target):
        if os.name == "nt":
            subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(path), str(target)],
                check=True,
                capture_output=True,
            )
        else:
            path.symlink_to(target, target_is_directory=True)

    linked = folder / "linked"
    link(linked, outside / "owned.outside")
    scope = PackMods(game, lambda _: None)
    try:
        scope.load(record)
        assert not scope.mods
    finally:
        scope.close()
        linked.rmdir() if os.name == "nt" else linked.unlink()
    folder.rmdir()
    link(folder, outside)
    scope = PackMods(game, lambda _: None)
    try:
        scope.load({**record, "root": str(outside.resolve())})
        assert not scope.mods
    finally:
        scope.close()
        folder.rmdir() if os.name == "nt" else folder.unlink()


def test_cleanup_failure_still_releases_the_other_instances(pack, code_mod):
    game, folder, _ = pack
    messages, cleaned = [], []
    scope = PackMods(game, messages.append)

    def fail():
        cleaned.append("failed")
        raise RuntimeError("owned cleanup failure")

    # Use the public cleanup argument on real instances, not a mock sandbox.
    healthy = Mod(
        Manifest.read(code_mod(folder, "owned.healthy")),
        report=lambda *_: None,
        cleanup=lambda: cleaned.append("healthy"),
    )
    failed = Mod(
        Manifest.read(code_mod(folder, "owned.failed")),
        report=lambda *_: None,
        cleanup=fail,
    )
    scope.mods.update({"owned.healthy": healthy, "owned.failed": failed})
    with pytest.raises(RuntimeError, match="Code mod cleanup failed"):
        scope.close()
    assert cleaned == ["failed", "healthy"]
    assert healthy.sandbox.closed and failed.sandbox.closed and not scope.mods
    assert any("CLEANUP ERROR" in line for line in messages)
    scope.close()
    assert cleaned == ["failed", "healthy"]


def test_real_python_mod_reports_only_with_its_admitted_identity(
    pack, built_python_mod
):
    game, folder, record = pack
    shutil.copytree(built_python_mod, folder / "python mod")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load(record)
        assert list(scope.mods) == ["example.diagnostics"]
        report = next(line for line in messages if line.startswith("CODE MOD REPORT:"))
        assert json.loads(report.split(": ", 1)[1]) == {
            "id": "example.diagnostics",
            "message": "ready  ",
        }
        assert scope.mods["example.diagnostics"].event("echo", 42) == {
            "count": 1,
            "value": 42,
        }


@pytest.mark.parametrize("cancel", [False, True])
def test_background_compilation_never_executes_or_leaks_a_cancelled_mod(
    pack, code_mod, monkeypatch, cancel
):
    from launcher import pack_mods

    game, folder, record = pack
    code_mod(folder, "owned.background")
    entered, release = threading.Event(), threading.Event()
    results, progress = [], []
    original = pack_mods._prepare

    def prepare(manifest, timings, cache=None):
        entered.set()
        assert release.wait(5), "Test did not release the compiler"
        result = original(manifest, timings, cache)
        results.append(result)
        return result

    monkeypatch.setattr(pack_mods, "_prepare", prepare)
    scope = PackMods(game, lambda _: None, background=True, set_loading=progress.append)
    try:
        scope.load(record)
        assert entered.wait(2) and not scope.mods and not scope.finished
        assert progress[-1] == dict(state="loading", loaded=0, failed=0, total=1)
        future = scope.pending
        if cancel:
            scope.close()
        release.set()
        future.result(timeout=5)
        if not cancel:
            scope.poll()
            assert scope.finished and list(scope.mods) == ["owned.background"]
            assert (
                scope.mods["owned.background"].sandbox._thread == threading.get_ident()
            )
            assert progress[-1] == dict(state="complete", loaded=1, failed=0, total=1)
    finally:
        release.set()
        scope.close()
        scope.executor.shutdown(wait=True)
    assert results[0].closed
    if cancel:
        assert not scope.mods and not any(
            row["state"] == "complete" for row in progress
        )
