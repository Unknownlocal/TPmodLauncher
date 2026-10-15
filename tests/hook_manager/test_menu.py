# SPDX-License-Identifier: MPL-2.0
"""Exercise native menu adaptation with an owned game interface, not game code or assets."""

import sys
import threading
from types import SimpleNamespace

import pytest

from hook_manager.menu import MenuAdapter, MenuRequests, menu_buttons


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
