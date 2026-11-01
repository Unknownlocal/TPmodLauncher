# SPDX-License-Identifier: MPL-2.0
"""Validate declarative menu buttons and adapt them to the game's native menu."""

import inspect
import re
import sys
import threading
import time
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


# These are the native action IDs, shared by mouse, keyboard and rendering.
SEASON_ACTIONS = frozenset({"New Season", "Continue Season", "Load Game"})
# Keep a stalled controller visible as a failure instead of allowing an unpatched season.
LOADING_TIMEOUT = 120


def loading_record(record):
    """Validate the manager's bounded progress record, never author-controlled status text."""
    if type(record) is not dict or set(record) != {
        "state",
        "loaded",
        "failed",
        "total",
    }:
        raise ValueError("Invalid loading status fields")
    if record["state"] not in ("loading", "complete", "error"):
        raise ValueError("Invalid loading state")
    if any(
        type(record[name]) is not int or not 0 <= record[name] <= 32
        for name in ("loaded", "failed", "total")
    ):
        raise ValueError("Invalid mod counts")
    if record["loaded"] + record["failed"] > record["total"]:
        raise ValueError("Mod counts exceed the discovered total")
    if (
        record["state"] == "complete"
        and record["loaded"] + record["failed"] != record["total"]
    ):
        raise ValueError("Loading cannot complete with unfinished mods")
    return dict(record)


class MenuAdapter:
    """Own native rows, readiness guards and a footer on the game's main thread."""

    def __init__(self, observe_screen=None):
        self.observe_screen = observe_screen
        self.buttons, self.installed = [], []
        self.owner = self.items = None
        self.originals, self.wrappers = {}, {}
        self.state, self.detail, self.generation = "idle", "", 0
        self.loading = None
        self.deadline = 0
        self.timed_out = False
        self.changed = None

    def _main(self):
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Menu changes require the game main thread")

    def set_loading(self, record):
        """Accept trusted progress; do not let a late completion undo a startup timeout."""
        self._main()
        record = loading_record(record)
        if self.loading is None:
            self.deadline = time.monotonic() + LOADING_TIMEOUT
        self.loading = record
        if self.owner is None and self.state != "failed":
            self.state = "pending"
        return self.poll()

    def _blocked(self):
        if self.loading is None:
            return False
        pending = self.loading["state"] == "loading" or self.state != "ready"
        if pending and not self.timed_out and time.monotonic() > self.deadline:
            self.timed_out = True
            if self.changed is not None:
                self.changed()
        return pending or self.timed_out

    def _season_blocked(self, item):
        """Apply startup blocking only to the three native season actions."""
        return item in SEASON_ACTIONS and self._blocked()

    def _notice(self):
        blocked = self._blocked()
        if self.timed_out:
            return "Code mod loading timed out - restart the launcher"
        if blocked:
            done = self.loading["loaded"] + self.loading["failed"]
            return f"Loading code mods: {done}/{self.loading['total']}"
        if self.loading["state"] == "error":
            return "Code mods unavailable - see launcher.log"
        if self.loading["failed"]:
            return f"Mods loaded: {self.loading['loaded']} ({self.loading['failed']} failed)"
        return (
            f"Mods loaded: {self.loading['loaded']}"
            if self.loading["total"]
            else "No code mods"
        )

    def update(self, records):
        """Replace only the declarations; keep the same startup readiness state."""
        self._main()
        buttons = menu_buttons(records)
        self._restore()
        self.generation += 1
        self.buttons, self.detail = buttons, ""
        self.state = "pending" if buttons or self.loading is not None else "idle"
        return self.poll()

    def _restore(self):
        """Restore only owned methods and rows; cached old wrappers become inactive."""
        conflict = False
        if self.owner is not None:
            for name, original in self.originals.items():
                current = inspect.getattr_static(self.owner, name, None)
                if current is self.wrappers.get(name):
                    setattr(self.owner, name, original)
                elif current is not original:
                    conflict = True
            for label in self.installed:
                if label in self.items:
                    self.items.remove(label)
        self.owner = self.items = None
        # Old cached wrappers still need their original methods for safe fall-through.
        self.originals, self.wrappers = {}, {}
        self.installed.clear()
        if conflict:
            raise RuntimeError("Menu methods were changed by another owner")

    def poll(self, completed=None):
        """Install after normal import, before its first consumer when observed by Manager."""
        self._main()
        if self.state != "pending":
            return self.status()
        hub = sys.modules.get("ui.screens.hub_screen")
        if hub is None or (
            getattr(getattr(hub, "__spec__", None), "_initializing", False)
            and completed is not hub
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
            methods = {"_activate": "(self, item)"}
            if self.loading is not None:
                methods.update(
                    {
                        "_item_enabled": "(self, item)",
                        "_disabled_item_message": "(self, item)",
                        "draw": "(self, surface)",
                    }
                )
            originals = {name: inspect.getattr_static(owner, name) for name in methods}
            if any(
                not inspect.isfunction(fn)
                or str(inspect.signature(fn)) != methods[name]
                or hasattr(fn, "__wrapped__")
                or hasattr(fn, "_tpm_menu_adapter")
                for name, fn in originals.items()
            ):
                raise ValueError("Unsupported main-menu methods")
            if not callable(inspect.getattr_static(owner, "draw")):
                raise ValueError("Unsupported main-menu drawing")
            if any(button["label"] in items for button in self.buttons):
                raise ValueError("Menu button conflicts with an existing label")
            clock = hub.pygame.time.get_ticks
            messages = {button["label"]: button["message"] for button in self.buttons}

            def active():
                # Replacement or cleanup gives the adapter a different wrapper group.
                return (
                    self.wrappers is wrappers
                    and threading.current_thread() is threading.main_thread()
                )

            def activate(screen, item):
                if active() and self.observe_screen is not None:
                    self.observe_screen(screen)
                if active():
                    message = (
                        self._notice()
                        if self._season_blocked(item)
                        else messages.get(item)
                    )
                    if message is not None:
                        screen.error_message, screen.error_until = (
                            message,
                            clock() + 2500,
                        )
                        return None
                result = originals["_activate"](screen, item)
                if active() and self.observe_screen is not None:
                    self.observe_screen(screen)
                return result

            wrappers = {"_activate": activate}
            if self.loading is not None:

                def enabled(screen, item):
                    if active() and self._season_blocked(item):
                        return False
                    return originals["_item_enabled"](screen, item)

                def disabled_message(screen, item):
                    if active() and self._season_blocked(item):
                        return self._notice()
                    return originals["_disabled_item_message"](screen, item)

                def draw(screen, surface):
                    if active() and self.observe_screen is not None:
                        self.observe_screen(screen)
                    result = originals["draw"](screen, surface)
                    if active() and not screen.is_loading:
                        # Reuse the native small font and scaling; leave its commercial notice intact.
                        margin = max(screen.dp(28), screen.ui_gap(18))
                        text = screen.fit_text(
                            self._notice(),
                            screen.small,
                            max(1, screen.width - 2 * margin),
                        )
                        label = screen.small.render(text, True, hub.WHITE)
                        bottom = (
                            screen.height
                            - margin
                            - screen.small.get_linesize()
                            - screen.ui_gap(6)
                        )
                        surface.blit(
                            label, label.get_rect(midbottom=(screen.width // 2, bottom))
                        )
                    return result

                wrappers.update(
                    {
                        "_item_enabled": enabled,
                        "_disabled_item_message": disabled_message,
                        "draw": draw,
                    }
                )
            self.owner, self.items, self.originals, self.wrappers = (
                owner,
                items,
                originals,
                wrappers,
            )
            for name, wrapper in wrappers.items():
                wrapper._tpm_menu_adapter = True
                setattr(owner, name, wrapper)
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
        """Return native registration and startup progress as plain bounded records."""
        result = {
            "state": self.state,
            "detail": self.detail,
            "generation": self.generation,
        }
        if self.loading is not None:
            result["loading"] = {
                **self.loading,
                "blocked": self._blocked(),
                "timed_out": self.timed_out,
            }
        return result

    def close(self):
        """Remove readiness and footer wrappers as well as mod-owned rows."""
        self._main()
        self.loading = None
        self.update([])
