# SPDX-License-Identifier: MPL-2.0
"""Check read-only startup discovery, folder boundaries and per-process snapshots."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from hook_manager.pack_discovery import PackDiscovery


@pytest.fixture
def game(tmp_path, monkeypatch):
    # Use owned modules with the relevant startup fields; no game code is imported.
    main, utils = ModuleType("__main__"), ModuleType("utils")

    def active_data_pack(default="owned-default"):
        raise AssertionError("Discovery must not call game helpers")

    utils.active_data_pack = main.active_data_pack = active_data_pack
    utils._ACTIVE_PACK_CACHE = {"owned-default": "owned pack"}
    main._CONSTANTS_LOADED = True
    monkeypatch.setitem(sys.modules, "__main__", main)
    monkeypatch.setitem(sys.modules, "utils", utils)
    (tmp_path / "data/owned pack").mkdir(parents=True)
    return SimpleNamespace(root=tmp_path, main=main, utils=utils)


def test_waits_for_startup_without_reading_preferences_or_importing_utils(
    game, monkeypatch
):
    game.main._CONSTANTS_LOADED = False
    monkeypatch.delitem(sys.modules, "utils")
    # A preference is not evidence that the running process loaded this pack.
    preference = game.root / "active_pack.json"
    preference.write_text('{"active_pack": "different pack"}')
    discovery = PackDiscovery(game.root)
    assert discovery.poll() == {"state": "pending"}
    assert "utils" not in sys.modules
    assert preference.read_text() == '{"active_pack": "different pack"}'
    monkeypatch.setitem(sys.modules, "utils", game.utils)
    game.main._CONSTANTS_LOADED = True
    assert discovery.poll()["id"] == "owned pack"


def test_snapshot_ignores_later_selection_and_is_not_shared_with_replacement(game):
    first = PackDiscovery(game.root)
    initial = first.poll()
    assert initial == {
        "state": "ready",
        "series": "formula",
        "id": "owned pack",
        "root": str((game.root / "data/owned pack").resolve()),
    }
    (game.root / "data/next pack").mkdir()
    game.utils._ACTIVE_PACK_CACHE.clear()
    assert first.poll() == initial
    game.utils._ACTIVE_PACK_CACHE["owned-default"] = "next pack"
    initial["id"] = "caller changed its copy"
    assert first.poll()["id"] == "owned pack"
    assert PackDiscovery(game.root).poll()["id"] == "next pack"
    first.stop()
    assert first.poll() == {"state": "stopped"}


def test_workshop_copy_uses_its_actual_game_data_folder_without_loading_contents(game):
    folder = game.root / "data/workshop_123456789"
    folder.mkdir()
    # Its presence does not authorize importing code or reading a future mod manifest.
    (folder / "__init__.py").write_text('raise AssertionError("pack code executed")')
    game.utils._ACTIVE_PACK_CACHE = {
        "unrelated-default": "wrong pack",
        "owned-default": folder.name,
    }
    record = PackDiscovery(game.root).poll()
    assert record["state"] == "ready" and record["root"] == str(folder.resolve())
    assert record["id"] == folder.name


@pytest.mark.parametrize(
    "name", ["../escape", "a/b", "a\\b", "C:escape", ".", "bad\nname", None]
)
def test_invalid_cached_ids_disable_discovery_without_guessing_a_pack(game, name):
    game.utils._ACTIVE_PACK_CACHE["owned-default"] = name
    discovery = PackDiscovery(game.root)
    assert discovery.poll()["state"] == "unavailable"
    # Correcting a cache later cannot silently reassign this process's startup pack.
    game.utils._ACTIVE_PACK_CACHE["owned-default"] = "owned pack"
    assert discovery.poll()["state"] == "unavailable"


def test_changed_game_interface_and_missing_folder_are_nonfatal(game):
    game.utils._ACTIVE_PACK_CACHE = []
    assert PackDiscovery(game.root).poll()["state"] == "unavailable"
    game.utils._ACTIVE_PACK_CACHE = {"owned-default": "missing"}
    assert PackDiscovery(game.root).poll()["state"] == "unavailable"


def test_link_cannot_redirect_pack_discovery_outside_data(game):
    outside = game.root / "outside"
    outside.mkdir()
    link = game.root / "data/redirected"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory links is unavailable on this system")
    game.utils._ACTIVE_PACK_CACHE["owned-default"] = link.name
    assert PackDiscovery(game.root).poll()["state"] == "unavailable"
