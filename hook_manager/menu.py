# SPDX-License-Identifier: MPL-2.0
"""Validate declarative menu buttons and adapt them to the game's native menu."""

import inspect
import re
import sys
import threading
from types import ModuleType


def menu_buttons(records):
    """Copy at most 32 owned buttons; accept text and identifiers, never executable actions."""
    if type(records) is not list or len(records) > 32:
        raise ValueError("Expected at most 32 menu buttons")
    result, keys, labels, counts = [], set(), set(), {}
    for record in records:
        if type(record) is not dict or set(record) != {
            "owner",
            "id",
            "label",
            "message",
        }:
            raise ValueError("Invalid menu button fields")
        for name in ("owner", "id"):
            value = record[name]
            if type(value) is not str or not re.fullmatch(
                r"[a-z][a-z0-9_.:-]{0,127}", value
            ):
                raise ValueError("Invalid menu button identifier")
        for name, limit in (("label", 64), ("message", 160)):
            value = record[name]
            if (
                type(value) is not str
                or not 1 <= len(value) <= limit
                or value != value.strip()
                or not value.isprintable()
            ):
                raise ValueError("Expected short, printable menu text")
        key = record["owner"], record["id"]
        counts[record["owner"]] = counts.get(record["owner"], 0) + 1
        if key in keys or record["label"] in labels or counts[record["owner"]] > 4:
            raise ValueError("Duplicate menu button or per-mod limit exceeded")
        keys.add(key)
        labels.add(record["label"])
        result.append(dict(record))
    return result


class MenuRequests:
    """Stage mod-owned registrations outside the game, before a bounded native update."""

    def __init__(self):
        self.buttons = []

    def add(self, owner, item_id, label, message):
        """Validate a complete proposed registry before committing a new button."""
        self.buttons = menu_buttons(
            self.buttons
            + [{"owner": owner, "id": item_id, "label": label, "message": message}]
        )

    def remove(self, owner):
        """Revoke only the calling mod's registrations during its owned cleanup."""
        self.buttons = [item for item in self.buttons if item["owner"] != owner]


class MenuAdapter:
    """Own one native activation wrapper and its rows, entirely on the game main thread."""

    def __init__(self):
        self.buttons = []
        self.installed = []
        self.owner = self.original = self.wrapper = self.items = None
        self.state = "idle"
        self.detail = ""
        self.generation = 0

    def _main(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Menu changes require the game main thread")

    def update(self, records):
        """Replace the approved declaration set; wait for the normal menu import if necessary."""
        self._main()
        buttons = menu_buttons(records)
        self._restore()
        self.generation += 1
        self.buttons, self.detail = buttons, ""
        self.state = "pending" if buttons else "idle"
        return self.poll()

    def _restore(self):
        """Undo only our rows and wrapper, preserving unrelated menu changes."""
        conflict = False
        if self.owner is not None:
            current = inspect.getattr_static(self.owner, "_activate", None)
            if current is self.wrapper:
                self.owner._activate = self.original
            elif current is not self.original:
                conflict = True
            for label in self.installed:
                if label in self.items:
                    self.items.remove(label)
        self.owner = self.original = self.wrapper = self.items = None
        self.installed.clear()
        if conflict:
            raise RuntimeError("Menu activation was changed by another owner")

    def poll(self):
        """Install after the native module finishes importing; unsupported menus fail visibly."""
        self._main()
        if self.state != "pending":
            return self.status()
        hub = sys.modules.get("ui.screens.hub_screen")
        if hub is None or getattr(
            getattr(hub, "__spec__", None), "_initializing", False
        ):
            return self.status()
        try:
            if type(hub) is not ModuleType:
                raise ValueError("Unsupported main-menu module")
            owner, items = vars(hub).get("HubScreen"), vars(hub).get("MENU_ITEMS")
            if (
                not isinstance(owner, type)
                or type(items) is not list
                or any(type(item) is not str for item in items)
            ):
                raise ValueError("Unsupported main-menu interface")
            original = inspect.getattr_static(owner, "_activate")
            if (
                not inspect.isfunction(original)
                or str(inspect.signature(original)) != "(self, item)"
                or hasattr(original, "__wrapped__")
                or hasattr(original, "_tpm_menu_adapter")
                or not callable(inspect.getattr_static(owner, "draw"))
            ):
                raise ValueError("Unsupported main-menu methods")
            if any(button["label"] in items for button in self.buttons):
                raise ValueError("Menu button conflicts with an existing label")
            clock = hub.pygame.time.get_ticks
            messages = {button["label"]: button["message"] for button in self.buttons}

            def activate(screen, item):
                if (
                    self.wrapper is activate
                    and threading.current_thread() is threading.main_thread()
                    and item in messages
                ):
                    # Reuse the native timed message; no mod code runs on this thread.
                    screen.error_message = messages[item]
                    screen.error_until = clock() + 2500
                    return None
                return original(screen, item)

            activate._tpm_menu_adapter = True
            # Record ownership before mutation so a failed installation can restore it.
            self.owner, self.original, self.wrapper, self.items = (
                owner,
                original,
                activate,
                items,
            )
            owner._activate = activate
            for button in self.buttons:
                label = button["label"]
                self.installed.append(label)
                items.insert(
                    items.index("Exit") if "Exit" in items else len(items), label
                )
            self.state = "ready"
        except Exception as error:
            self.state, self.detail = "failed", str(error)[:512]
            self._restore()
        return self.status()

    def status(self):
        """Return bounded readiness data, never native menu objects."""
        return {
            "state": self.state,
            "detail": self.detail,
            "generation": self.generation,
        }

    def close(self):
        """Remove our native changes before the trusted bridge shuts down."""
        self.update([])
