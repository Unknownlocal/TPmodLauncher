# SPDX-License-Identifier: MPL-2.0
"""Owned menu interface imported normally by the native bridge target."""

from types import SimpleNamespace


class HubScreen:
    def _item_enabled(self, item):
        return item != "Continue Season"

    def _disabled_item_message(self, item):
        return "native disabled message"

    def _activate(self, item):
        return "native " + item

    def draw(self, surface):
        return None


MENU_ITEMS = ["Career", "Exit"]
pygame = SimpleNamespace(time=SimpleNamespace(get_ticks=lambda: 10))
# Capture originals before the import observer can replace them.
ORIGINAL_METHODS = {
    name: getattr(HubScreen, name)
    for name in ("_activate", "draw", "_item_enabled", "_disabled_item_message")
}
