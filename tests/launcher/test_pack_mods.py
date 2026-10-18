# SPDX-License-Identifier: MPL-2.0
"""Load selected-pack child mods using real manifests, filesystem paths and Wasmtime."""

from contextlib import closing
import json
import os
import subprocess

import pytest

from launcher.pack_mods import PackMods


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


def test_untrusted_pack_records_cannot_choose_another_folder(pack, code_mod):
    game, folder, record = pack
    code_mod(folder, "owned.valid")
    messages = []
    with closing(PackMods(game, messages.append)) as scope:
        scope.load({**record, "id": "../escape"})
        assert not scope.mods and "DISCOVERY REJECTED" in messages[0]
        scope.load(record)  # A rejected snapshot is not reassigned later.
        assert not scope.mods and len(messages) == 1


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
