# SPDX-License-Identifier: MPL-2.0
"""Load selected-pack child mods using real manifests, filesystem paths and Wasmtime."""

from contextlib import closing
import json
import os
import subprocess
import queue

import pytest

from launcher.pack_mods import PackMods
from launcher.diagnostics import reporter, handle_error
from launcher.mod_contract import Mod
from tests.ui_support import ui_sink


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


def test_fatal_loading_progress_failure_is_logged_without_a_second_bridge_request(pack):
    game, _, record = pack
    failure = TimeoutError("Owned progress request timed out " + "x" * 7000 + " FULL END")
    requests = []
    def stalled_progress(value):
        requests.append(value)
        raise failure
    path = game / "launcher.log"
    with reporter(path) as logger:
        with closing(PackMods(game, logger, set_loading=stalled_progress)) as scope:
            with pytest.raises(TimeoutError) as caught:
                scope.load(record)
            assert caught.value is failure and len(requests) == 1
            handle_error(logger, caught.value, stage="controller")
    text = path.read_text()
    assert "CODE MOD LOADING FAILED" in text and "FULL END" in text


@pytest.mark.parametrize("secondary", ["progress", "cleanup"])
def test_original_mod_failure_survives_failed_cleanup_or_progress(pack, code_mod, monkeypatch, secondary):
    game, folder, record = pack
    code_mod(folder, "owned.failure")
    original = RuntimeError("Original activation failure " + "x" * 7000 + " PRIMARY END")
    def failed_activation(mod):
        try:
            raise OSError("Owned complete activation cause")
        except OSError as cause:
            if secondary == "progress":
                raise original
            raise original from cause
    monkeypatch.setattr(Mod, "activate", failed_activation)
    close = Mod.close
    def failed_close(mod):
        close(mod)
        raise OSError("Owned cleanup secondary failure")
    if secondary == "cleanup":
        monkeypatch.setattr(Mod, "close", failed_close)
    progress = []
    def publish(value):
        progress.append(value)
        if secondary == "progress" and value["failed"]:
            raise TimeoutError("Owned progress secondary failure")
    path, outcome = game / "launcher.log", {}
    with reporter(path, outcome=outcome) as logger:
        with closing(PackMods(game, logger, set_loading=publish)) as scope:
            with pytest.raises(RuntimeError) as caught:
                scope.load(record)
            assert caught.value is original
            cause = original.__context__ if secondary == "progress" else original.__cause__
            assert isinstance(cause, OSError) and str(cause) == "Owned complete activation cause"
            handle_error(logger, caught.value, stage="controller")
            # Later fatal cleanup reporting must remain secondary to this load.
            handle_error(logger, OSError("Owned final cleanup failure"), stage="cleanup")
    assert outcome["failure"]["reason"] == str(original)
    assert outcome["failure"]["stage"] == "controller"
    assert outcome["failure"]["activity"]["mod"] == "owned.failure"
    text = path.read_text()
    assert "PRIMARY END" in text and "Owned complete activation cause" in text
    assert f"Owned {secondary} secondary failure" in text and "Owned final cleanup failure" in text
    assert len(progress) == (2 if secondary == "progress" else 1)


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


def test_independent_activation_cannot_inherit_another_areas_ui_failure(pack, code_mod, monkeypatch):
    game, folder, record = pack
    for owner, capability in (("owned.a", "ui-settings"), ("owned.b", "main-menu")):
        root = code_mod(folder, owner)
        manifest = root / "mod.toml"
        text = manifest.read_text().replace('api = "0.1.0"', 'api = "0.3.0"')
        text = text.replace('["diagnostics"]', '["diagnostics", "' + capability + '"]')
        if owner == "owned.a":
            text += '\n[prerequisites]\nsettings = ["mod:owned.b"]\n'
        manifest.write_text(text)
    activate = Mod.activate
    def register(mod):
        activate(mod)  # Real small Wasm components avoid an author-Python build.
        if mod.manifest.id == "owned.a":
            scope.settings.call("owned.a", "register-settings", json.dumps({"schema": 1, "label": "Owned settings",
                "fields": [{"id": "accent", "label": "Accent", "color": "#112233"}]}))
        else:
            scope.menu.add("owned.b", "open", "Owned menu", "Owned message")
    monkeypatch.setattr(Mod, "activate", register)
    menus, settings = [], []
    def publish(rows, result=None):
        settings.append(rows)
        return {"settings": {"generation": len(settings), "state": "failed" if rows else "idle",
                             "detail": "Owned unavailable settings integration"}}
    with closing(PackMods(game, lambda _: None, set_ui=ui_sink(menu=menus.append, settings=publish))) as scope:
        scope.load(record)
        assert list(scope.mods) == ["owned.b"] and scope.failed == 1
        assert menus[-1][0]["owner"] == "owned.b" and settings[-1] == []
        assert scope.mods["owned.b"].event("echo", None) == {}


def test_background_components_resume_on_read_and_compile_completion(pack, code_mod):
    game, folder, record = pack
    broken = code_mod(folder, "owned.broken")
    (broken / "mod.wasm").unlink()
    code_mod(folder, "owned.healthy")
    completed, messages = queue.Queue(), []
    with closing(PackMods(game, messages.append, background=True,
                          wake=lambda: completed.put(None))) as scope:
        scope.load(record)
        while scope.loading is not None:
            completed.get(timeout=5)
            scope.poll()
        assert scope.finished and scope.failed == 1
        assert list(scope.mods) == ["owned.healthy"]
        assert scope.mods["owned.healthy"].event("owned.event", None) == {}
        assert any("REJECTED" in line and "owned.broken" in line for line in messages)


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


def test_untrusted_pack_records_cannot_choose_another_folder(pack, code_mod):
    game, folder, record = pack
    code_mod(folder, "owned.valid")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load({**record, "id": "../escape"})
        assert not scope.mods and "DISCOVERY REJECTED" in messages[0]
        before = list(messages)
        scope.load(record)  # A rejected snapshot is not reassigned later.
        assert not scope.mods and messages == before


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
