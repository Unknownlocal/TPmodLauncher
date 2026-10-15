# SPDX-License-Identifier: MPL-2.0
"""Load selected-pack child mods using real manifests, filesystem paths and Wasmtime."""

from contextlib import closing
import json
import os
import shutil
import subprocess

import pytest

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
        assert json.loads(messages[-1].split(": ", 1)[1])["active"] == 0


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
