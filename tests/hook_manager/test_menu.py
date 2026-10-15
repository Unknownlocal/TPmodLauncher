# SPDX-License-Identifier: MPL-2.0
"""Exercise native menu adaptation with an owned game interface, not game code or assets."""

import sys
import threading
from types import SimpleNamespace

import pytest

from hook_manager.menu import MenuAdapter, MenuRequests, menu_buttons, loading_record


def button(owner="owned.menu", item_id="proof", label="Mod Launcher"):
    return {
        "owner": owner,
        "id": item_id,
        "label": label,
        "message": "UI mod is active",
    }


def test_native_rows_click_confirmation_and_owned_cleanup(native_menu):
    hub, calls = native_menu
    original = hub.HubScreen._activate
    adapter = MenuAdapter()
    try:
        assert (
            adapter.update([button(), button("owned.second", label="Second")])["state"]
            == "ready"
        )
        assert hub.MENU_ITEMS == ["Career", "Load", "Mod Launcher", "Second", "Exit"]
        screen = hub.HubScreen()
        assert screen._activate("Mod Launcher") is None
        assert screen.error_message == "UI mod is active" and screen.error_until == 2510
        assert screen._activate("Exit") == "native activation"
        assert screen.draw("surface") == "native draw"
        assert calls == ["Exit", ("draw", "surface")]
        saved = screen._activate
        # Replacing one owner's declaration leaves the remaining mod's row usable.
        adapter.update([button("owned.second", label="Second")])
        assert "Mod Launcher" not in hub.MENU_ITEMS and "Second" in hub.MENU_ITEMS
        hub.MENU_ITEMS.insert(0, "Unrelated")
    finally:
        adapter.close()
    assert hub.HubScreen._activate is original
    assert hub.MENU_ITEMS == ["Unrelated", "Career", "Load", "Exit"]
    assert saved("Mod Launcher") == "native activation"


def test_cleanup_preserves_a_foreign_method_and_revokes_cached_callbacks(native_menu):
    hub, _ = native_menu
    adapter = MenuAdapter()
    adapter.update([button()])
    cached = hub.HubScreen()._activate

    def foreign(self, item):
        return "foreign"

    hub.HubScreen._activate = foreign
    with pytest.raises(RuntimeError, match="another owner"):
        adapter.close()
    assert hub.HubScreen._activate is foreign
    assert "Mod Launcher" not in hub.MENU_ITEMS
    assert cached("Mod Launcher") == "native activation"
    adapter.close()


def test_menu_waits_for_import_and_reports_an_unsupported_interface(
    native_menu, monkeypatch
):
    hub, _ = native_menu
    adapter = MenuAdapter()
    monkeypatch.delitem(sys.modules, hub.__name__)
    assert adapter.update([button()])["state"] == "pending"
    monkeypatch.setitem(sys.modules, hub.__name__, hub)
    hub.__spec__ = SimpleNamespace(_initializing=True)
    assert adapter.poll()["state"] == "pending"
    hub.__spec__._initializing = False
    hub.HubScreen._activate = None
    assert adapter.poll()["state"] == "failed"
    assert hub.MENU_ITEMS == ["Career", "Load", "Exit"]
    adapter.close()


@pytest.mark.parametrize(
    "change",
    [
        {"owner": "../escape"},
        {"id": "bad()"},
        {"label": "line\nbreak"},
        {"label": "x" * 65},
        {"message": "x" * 161},
        {"action": "os.system"},
    ],
)
def test_invalid_declarations_are_rejected(change):
    with pytest.raises(ValueError):
        menu_buttons([{**button(), **change}])


def test_registry_ownership_uniqueness_and_quota():
    requests = MenuRequests()
    for number in range(4):
        requests.add("owned.a", f"item{number}", f"Item {number}", "Message")
    with pytest.raises(ValueError):
        requests.add("owned.a", "fifth", "Fifth", "Message")
    with pytest.raises(ValueError):
        requests.add("owned.b", "other", "Item 0", "Message")
    requests.add("owned.b", "other", "Other", "Message")
    requests.remove("owned.a")
    assert [item["owner"] for item in requests.buttons] == ["owned.b"]


def test_existing_native_labels_and_worker_mutations_are_rejected(native_menu):
    hub, _ = native_menu
    adapter = MenuAdapter()
    assert adapter.update([button(label="Exit")])["state"] == "failed"
    assert hub.MENU_ITEMS == ["Career", "Load", "Exit"]
    errors = []

    def worker():
        try:
            adapter.update([button()])
        except RuntimeError as error:
            errors.append(str(error))

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join(timeout=2)
    assert errors == ["Menu changes require the game main thread"]
    adapter.close()


def test_readiness_blocks_only_season_actions_until_native_registration(
    native_menu, monkeypatch
):
    hub, calls = native_menu
    hub.MENU_ITEMS[:] = [
        "New Season",
        "Continue Season",
        "Load Game",
        "Options",
        "Exit",
    ]
    adapter = MenuAdapter()
    original = {
        name: getattr(hub.HubScreen, name)
        for name in ("draw", "_activate", "_item_enabled", "_disabled_item_message")
    }
    loading = dict(state="loading", loaded=0, failed=0, total=1)
    try:
        assert adapter.set_loading(loading)["loading"]["blocked"]
        screen = hub.HubScreen()
        cached = screen._item_enabled
        for item in ("New Season", "Continue Season", "Load Game"):
            assert screen._item_enabled(item) is False
            assert screen._activate(item) is None
            assert "Loading code mods" in screen._disabled_item_message(item)
        assert not calls
        assert screen._item_enabled("Options") is True
        assert screen._activate("Options") == "native activation"
        # Completion before a normal menu import must not claim readiness yet.
        monkeypatch.delitem(sys.modules, hub.__name__)
        adapter.update([button()])
        pending = adapter.set_loading({**loading, "state": "complete", "loaded": 1})
        assert pending["state"] == "pending" and pending["loading"]["blocked"]
        monkeypatch.setitem(sys.modules, hub.__name__, hub)
        assert not adapter.poll()["loading"]["blocked"]
        assert screen._item_enabled("New Season") is True
        assert screen._item_enabled("Load Game") is True
        assert (
            screen._item_enabled("Continue Season") is False
        )  # Preserve the native no-save rule.
        assert adapter._notice() == "Mods loaded: 1"
    finally:
        adapter.close()
    assert all(
        getattr(hub.HubScreen, name) is method for name, method in original.items()
    )
    assert cached("New Season") is True


@pytest.mark.parametrize(
    "state,loaded,failed,total,text",
    [
        ("complete", 0, 0, 0, "No code mods"),
        ("complete", 1, 1, 2, "Mods loaded: 1 (1 failed)"),
        ("error", 0, 0, 0, "Code mods unavailable - see launcher.log"),
    ],
)
def test_loader_footer_uses_native_font_and_leaves_original_drawing(
    native_menu, state, loaded, failed, total, text
):
    hub, calls = native_menu
    adapter = MenuAdapter()
    drawn = []
    label = SimpleNamespace(get_rect=lambda **position: position)

    class Font:
        def get_linesize(self):
            return 20

        def render(self, value, antialias, colour):
            drawn.append(value)
            return label

    hub.WHITE = (255, 255, 255)
    screen = hub.HubScreen()
    screen.small, screen.width, screen.height, screen.is_loading = (
        Font(),
        1000,
        800,
        False,
    )
    screen.dp = screen.ui_gap = lambda value: value
    screen.fit_text = lambda text, font, width: text
    surface = SimpleNamespace(blit=lambda image, rect: drawn.append(rect))
    try:
        adapter.set_loading(
            dict(state=state, loaded=loaded, failed=failed, total=total)
        )
        assert screen.draw(surface) == "native draw"
        assert calls == [("draw", surface)]
        assert drawn == [text, {"midbottom": (500, 746)}]
        assert not screen._item_enabled("Continue Season")
    finally:
        adapter.close()


@pytest.mark.parametrize(
    "change",
    [
        {"loaded": True},
        {"failed": -1},
        {"total": 33},
        {"state": "ready"},
        {"state": "complete"},
        {"loaded": 2},
        {"message": "Untrusted text"},
    ],
)
def test_invalid_progress_cannot_release_readiness(native_menu, change):
    adapter = MenuAdapter()
    loading = dict(state="loading", loaded=0, failed=0, total=1)
    try:
        adapter.set_loading(loading)
        with pytest.raises(ValueError):
            adapter.set_loading({**loading, **change})
        assert adapter.status()["loading"]["blocked"]
        assert loading_record(loading) == loading
    finally:
        adapter.close()


def test_loader_timeout_stays_visible_and_cannot_be_undone_by_late_completion(
    native_menu, monkeypatch
):
    import hook_manager.menu as menu

    now = [0.0]
    monkeypatch.setattr(menu, "time", SimpleNamespace(monotonic=lambda: now[0]))
    adapter = MenuAdapter()
    try:
        adapter.set_loading(dict(state="loading", loaded=0, failed=0, total=1))
        now[0] = 121.0
        assert adapter.status()["loading"]["timed_out"]
        assert "timed out" in adapter._notice()
        assert adapter.set_loading(dict(state="complete", loaded=1, failed=0, total=1))[
            "loading"
        ]["blocked"]
        screen = native_menu[0].HubScreen()
        assert not screen._item_enabled("New Season")
        assert screen._item_enabled("Options")
    finally:
        adapter.close()
