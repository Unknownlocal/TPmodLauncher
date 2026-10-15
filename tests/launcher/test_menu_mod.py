# SPDX-License-Identifier: MPL-2.0
"""Run the sandboxed UI example beside a diagnostics mod through the real capability path."""

from contextlib import closing
import json
import shutil
import sys

import pytest

from hook_manager.menu import MenuAdapter
from launcher.mod_contract import Manifest, ManifestError, Mod
from launcher.pack_mods import PackMods
from launcher.sandbox import SandboxError


@pytest.fixture
def menu_pack(tmp_path, built_menu_mod, built_python_mod):
    pack = tmp_path / "data/owned pack"
    shutil.copytree(built_menu_mod, pack / "ui mod")
    shutil.copytree(built_python_mod, pack / "diagnostic mod")
    record = {
        "state": "ready",
        "series": "formula",
        "id": pack.name,
        "root": str(pack.resolve()),
    }
    return tmp_path, record


def test_two_python_mods_coexist_and_ui_cleanup_restores_native_menu(
    menu_pack, native_menu
):
    game, record = menu_pack
    hub, _ = native_menu
    adapter, messages = MenuAdapter(), []
    original = hub.HubScreen._activate
    with closing(
        PackMods(
            game,
            messages.append,
            set_menu=lambda items: {"menu": adapter.update(items)},
        )
    ) as scope:
        scope.load(record)
        assert list(scope.mods) == ["example.diagnostics", "example.menu"]
        timings = [
            json.loads(line.split(": ", 1)[1])
            for line in messages
            if line.startswith("CODE MOD TIMING:")
        ]
        assert [row["id"] for row in timings] == ["example.diagnostics", "example.menu"]
        assert "registration" not in timings[0]["phase_seconds"]
        assert timings[1]["phase_seconds"]["registration"] >= 0
        assert hub.MENU_ITEMS == ["Career", "Load", "Mod Launcher", "Exit"]
        screen = hub.HubScreen()
        screen._activate("Mod Launcher")
        assert screen.error_message == "The sandboxed UI mod is active"
        # A stale failure from before this registration cannot disable a healthy UI mod.
        scope.menu_status({"state": "failed", "detail": "stale", "generation": 0})
        assert len(scope.mods) == 2
        assert scope.mods["example.diagnostics"].event("echo", 42) == {
            "count": 1,
            "value": 42,
        }
        # A later guest-call failure revokes the native button without affecting diagnostics.
        with pytest.raises(SandboxError):
            scope.mods["example.menu"].event("invalid/event", None)
        assert list(scope.mods) == ["example.diagnostics"]
        assert "Mod Launcher" not in hub.MENU_ITEMS
    assert hub.MENU_ITEMS == ["Career", "Load", "Exit"]
    assert hub.HubScreen._activate is original
    assert len([line for line in messages if "ACTIVE:" in line]) == 2


def test_failed_deferred_menu_disables_only_ui_mods(
    menu_pack, native_menu, monkeypatch
):
    game, record = menu_pack
    hub, _ = native_menu
    monkeypatch.delitem(sys.modules, hub.__name__)
    adapter = MenuAdapter()
    with closing(
        PackMods(
            game, lambda _: None, set_menu=lambda items: {"menu": adapter.update(items)}
        )
    ) as scope:
        scope.load(record)
        assert len(scope.mods) == 2 and adapter.state == "pending"
        hub.HubScreen._activate = None
        monkeypatch.setitem(sys.modules, hub.__name__, hub)
        scope.menu_status(adapter.poll())
        assert list(scope.mods) == ["example.diagnostics"]
        assert not scope.menu.buttons
        assert scope.mods["example.diagnostics"].event("echo", None)["count"] == 1


def test_native_label_conflict_rejects_only_the_ui_mod(menu_pack, native_menu):
    game, record = menu_pack
    hub, _ = native_menu
    hub.MENU_ITEMS.insert(0, "Mod Launcher")
    adapter = MenuAdapter()
    with closing(
        PackMods(
            game, lambda _: None, set_menu=lambda items: {"menu": adapter.update(items)}
        )
    ) as scope:
        scope.load(record)
        assert list(scope.mods) == ["example.diagnostics"]
        assert hub.MENU_ITEMS.count("Mod Launcher") == 1


def test_menu_permission_and_provider_are_required(built_menu_mod, tmp_path):
    folder = tmp_path / "owned"
    shutil.copytree(built_menu_mod, folder)
    manifest = Manifest.read(folder)
    with pytest.raises(ManifestError, match="trusted menu provider"):
        Mod(manifest, report=lambda *_: None)
    path = folder / "mod.toml"
    path.write_text(path.read_text().replace(', "main-menu"', ""))
    with pytest.raises(SandboxError, match="approved host operation"):
        Mod(Manifest.read(folder), report=lambda *_: None)
    path.write_text(
        path.read_text()
        .replace('api = "0.2.0"', 'api = "0.1.0"')
        .replace('["diagnostics"]', '["diagnostics", "main-menu"]')
    )
    with pytest.raises(ManifestError, match="capability"):
        Manifest.read(folder)
