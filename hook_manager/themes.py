# SPDX-License-Identifier: MPL-2.0
"""Declarative palettes and owned integration with the game's restart-only themes."""

import inspect
import json
import re
import sys
import threading


THEME_ROLES = frozenset("""
BACKGROUND HEADER PANEL PANEL_ALT PANEL_LIGHT CARD CARD_ALT CARD_HOVER BORDER BORDER_HOVER
PRIMARY PRIMARY_HOVER PRIMARY_DARK PRIMARY_SOFT NAV_PRIMARY NAV_PRIMARY_HOVER NAV_PRIMARY_PRESSED
TEXT TEXT_MUTED TEXT_DIM SUCCESS SUCCESS_HOVER SUCCESS_DARK WARNING WARNING_DARK
DANGER DANGER_HOVER DANGER_DARK NOTICE_FILL NOTICE_BORDER NOTICE_TEXT DISABLED DISABLED_TEXT
NEUTRAL_BUTTON NEUTRAL_BUTTON_HOVER BAR_BACKGROUND TYRE_CORNERING TYRE_TRACTION TYRE_DURABILITY
TYRE_TEMPERATURE SELECTION_FILL SELECTION_FILL_HOVER SELECTION_BORDER SELECTION_TEXT
MARKET_BACKGROUND MARKET_CARD MARKET_CARD_ALT MARKET_HEADER MARKET_BORDER TABLE_ROW TABLE_ROW_ALT
TABLE_HEADER TABLE_GRID RATING_GREEN_LOW RATING_GREEN_MID RATING_GREEN_HIGH MARKET_RATING_RED
MARKET_RATING_YELLOW OVERLAY
""".split())


def theme_definition(data):
    if type(data) is not dict or set(data) != {"label", "colours"}:
        raise ValueError("Invalid theme definition")
    label, colours = data["label"], data["colours"]
    if (type(label) is not str or not 1 <= len(label) <= 64 or label != label.strip() or not label.isprintable()
            or type(colours) is not dict or colours.keys() != THEME_ROLES):
        raise ValueError("Expected a labelled complete UITheme palette")
    for role, value in colours.items():
        digits = 8 if role == "OVERLAY" else 6
        if type(value) is not str or re.fullmatch(r"#[0-9a-fA-F]{%d}" % digits, value) is None:
            raise ValueError("Invalid theme colour")
    return {"label": label, "colours": {role: value.upper() for role, value in colours.items()}}


class ThemeRequests:
    """One named palette per mod, with host-owned identity and no executable targets."""

    def __init__(self):
        self._records = {}

    def register(self, owner, text):
        if type(text) is not str or len(text.encode("utf-8")) > 4096:
            raise ValueError("Expected at most 4096 bytes of theme JSON")
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("Duplicate theme member")
                result[key] = value
            return result
        data = theme_definition(json.loads(text, object_pairs_hook=unique))
        if owner not in self._records and len(self._records) >= 4:
            raise ValueError("At most four custom themes may register")
        if any(record["label"] == data["label"] for key, record in self._records.items() if key != owner):
            raise ValueError("Custom theme labels must be unique")
        self._records[owner] = {"owner": owner, **data}

    @property
    def records(self):
        return [{**record, "colours": dict(record["colours"])} for _, record in sorted(self._records.items())]

    def remove(self, owner):
        self._records.pop(owner, None)


class _ThemeColour(tuple):
    """Colours already resolved by role must bypass chrome's source-colour lookup."""


class ThemeAdapter:
    def __init__(self):
        self.records = []
        self.state, self.detail, self.generation = "idle", "", 0
        self.mutations, self.labels, self.choices = [], [], []
        self.module = None
        self.applied = None
        self.startup_choice = None
        self.resolved = {}
        self.getitem = None
        self.armed = self.loading_complete = False
        self.placeholder = None

    @staticmethod
    def _main():
        if threading.current_thread() is not threading.main_thread():
            raise RuntimeError("Theme changes require the game main thread")

    def _set(self, owner, name, value):
        previous = inspect.getattr_static(owner, name)
        setattr(owner, name, value)
        self.mutations.append((owner, name, previous, value))

    def update(self, records):
        self._main()
        if type(records) is not list or len(records) > 4:
            raise ValueError("Expected at most four custom themes")
        copied, owners, labels = [], set(), set()
        for row in records:
            if type(row) is not dict or set(row) != {"owner", "label", "colours"}:
                raise ValueError("Invalid native theme record")
            owner = row["owner"]
            data = theme_definition({key: row[key] for key in ("label", "colours")})
            if (type(owner) is not str or re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", owner) is None
                    or owner in owners or data["label"] in labels):
                raise ValueError("Invalid or duplicate theme owner/label")
            owners.add(owner)
            labels.add(data["label"])
            copied.append({"owner": owner, **data})
        # Saved edits do not recolour a running game. Keep the startup palette until restart.
        if self.applied is not None and self.applied not in owners:
            self._restore()
        self._remove_labels()
        self.records = copied
        self.generation += 1
        self.state, self.detail = ("pending" if copied else "idle"), ""
        return self.poll()

    def _remove_labels(self):
        for mapping, key, value in self.labels:
            if mapping.get(key) == value:
                del mapping[key]
        self.labels.clear()
        for owner, previous, value in reversed(self.choices):
            if owner.COLOR_PACKS is value:
                owner.COLOR_PACKS = previous
        self.choices.clear()

    def _restore(self):
        self.getitem = None
        for owner, name, previous, value in reversed(self.mutations):
            if inspect.getattr_static(owner, name, None) is value:
                setattr(owner, name, previous)
        self.mutations.clear()
        # Native modules imported after application may have captured UITheme aliases.
        for owner in self._native_owners():
            for name, value in list(vars(owner).items()):
                original = self.resolved.get(id(value))
                if name.isupper() and original is not None:
                    setattr(owner, name, original)
        self.resolved.clear()
        self.applied = None
        if self.module is not None:
            self.module.CHROME.clear()

    def _choices(self, module):
        keys = tuple(module.COLOR_PACK_LABELS)
        owners = [module]
        options = sys.modules.get("ui.screens.options_screen")
        if options is not None and not getattr(getattr(options, "__spec__", None), "_initializing", False):
            owners.append(options)
        for owner in owners:
            if owner.COLOR_PACKS != keys:
                self.choices.append((owner, owner.COLOR_PACKS, keys))
                owner.COLOR_PACKS = keys

    def has_choices(self):
        return bool(self.records or self.placeholder is not None)

    def _arm(self, completed=None):
        """Preserve the startup ID before native display initialization normalizes/saves it."""
        module = sys.modules.get("ui.color_packs")
        if self.armed or module is None or (getattr(getattr(module, "__spec__", None), "_initializing", False)
                                           and module is not completed):
            return
        if type(module.COLOR_PACK_LABELS) is not dict or type(module.COLOR_PACKS) is not tuple:
            raise ValueError("Unsupported native theme interface")
        try:
            with module.graphics_settings_path().open("r", encoding="utf-8-sig") as source:
                raw = source.read(65537)
            data = json.loads(raw) if len(raw) <= 65536 else {}
            choice = data.get("color_pack") if type(data) is dict else None
            self.startup_choice = choice.strip().lower() if type(choice) is str else "blue"
        except (OSError, ValueError):
            self.startup_choice = module.ACTIVE_COLOR_PACK
        self.module, self.armed = module, True
        if (not self.loading_complete and type(self.startup_choice) is str
                and re.fullmatch(r"code-mod:[a-z][a-z0-9_.:-]{0,127}", self.startup_choice)
                and self.startup_choice not in module.COLOR_PACK_LABELS):
            label = "Custom Theme (loading)"
            module.COLOR_PACK_LABELS[self.startup_choice] = label
            self.placeholder = module.COLOR_PACK_LABELS, self.startup_choice, label
            self._choices(module)

    def _drop_placeholder(self):
        if self.placeholder is not None:
            mapping, key, value = self.placeholder
            if mapping.get(key) == value:
                del mapping[key]
            self.placeholder = None

    def finish_loading(self):
        self._main()
        if self.loading_complete:
            return False
        self.loading_complete = True
        changed = self.placeholder is not None
        self._drop_placeholder()
        if self.module is not None:
            self._choices(self.module)
        return changed

    def poll(self, completed=None):
        self._main()
        try:
            self._arm(completed)
        except Exception as error:
            self.state, self.detail = "failed", str(error)[:512]
        if self.state not in ("pending", "ready"):
            if self.module is not None:
                self._choices(self.module)
            return self.status()
        module, theme = sys.modules.get("ui.color_packs"), sys.modules.get("ui.theme")
        if any(item is None or (getattr(getattr(item, "__spec__", None), "_initializing", False) and item is not completed)
               for item in (module, theme)):
            return self.status()
        try:
            if self.state == "pending":
                if type(module.COLOR_PACK_LABELS) is not dict or type(module.COLOR_PACKS) is not tuple:
                    raise ValueError("Unsupported native theme interface")
                original = {role: getattr(theme.UITheme, role) for role in THEME_ROLES}
                for role, colour in original.items():
                    if (not isinstance(colour, tuple) or len(colour) != (4 if role == "OVERLAY" else 3)
                            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in colour)):
                        raise ValueError("Unsupported native UITheme palette")
                for record in self.records:
                    key = "code-mod:" + record["owner"]
                    if self.placeholder is not None and self.placeholder[1] == key:
                        self._drop_placeholder()
                    if key in module.COLOR_PACK_LABELS or record["label"] in module.COLOR_PACK_LABELS.values():
                        raise ValueError("Custom theme conflicts with a native theme")
                    module.COLOR_PACK_LABELS[key] = record["label"]
                    self.labels.append((module.COLOR_PACK_LABELS, key, record["label"]))
                if self.applied is None:
                    record = next((row for row in self.records if "code-mod:" + row["owner"] == self.startup_choice), None)
                    if record is not None:
                        self._apply(module, original, record)
                self.state = "ready"
            self._choices(module)
        except Exception as error:
            self.state, self.detail = "failed", str(error)[:512]
            self._remove_labels()
            self._restore()
        return self.status()

    def _apply(self, module, original, record):
        colours = {role: _ThemeColour(int(value[start:start + 2], 16) for start in range(1, len(value), 2))
                   for role, value in record["colours"].items()}
        mapping = {}
        for role, value in original.items():
            if value in mapping and mapping[value] != colours[role]:
                raise ValueError("Roles sharing a native colour must remain coupled")
            mapping[value] = colours[role]
        self.resolved = {id(value): original[role] for role, value in colours.items()}
        # Rebind imported native UI colour aliases as well as UITheme. Later imports
        # receive resolved tuples directly. Never walk instance state or artwork.
        for owner in self._native_owners():
            for key, value in list(vars(owner).items()):
                if (key.isupper() and type(value) is tuple and len(value) in (3, 4)
                        and all(type(channel) is int for channel in value) and value in mapping):
                    self._set(owner, key, mapping[value])
        original_getitem = inspect.getattr_static(module.ChromePalette, "__getitem__")
        def getitem(palette, colour):
            if self.getitem is getitem and palette is module.CHROME:
                if isinstance(colour, _ThemeColour):
                    return tuple(colour)
                key = tuple(colour) if type(colour) is list else colour
                if type(key) is tuple and all(type(channel) is int for channel in key):
                    return tuple(mapping.get(key, key))
            return original_getitem(palette, colour)
        self._set(module.ChromePalette, "__getitem__", getitem)
        self.getitem = getitem
        module.CHROME.clear()
        self.applied = record["owner"]

    @staticmethod
    def _native_owners():
        seen = set()
        for name, imported in list(sys.modules.items()):
            if not name.startswith("ui.") or imported is None:
                continue
            owners = [imported] + [value for value in vars(imported).values()
                                   if isinstance(value, type) and value.__module__ == name]
            for owner in owners:
                if id(owner) in seen:
                    continue
                seen.add(id(owner))
                yield owner

    def status(self):
        return {"state": self.state, "detail": self.detail, "generation": self.generation}

    def prepare_screen(self, screen):
        """Keep an open native dropdown valid as its owned choices are replaced."""
        if self.module is None:
            return
        keys = tuple(self.module.COLOR_PACK_LABELS)
        if getattr(screen, "_tpm_theme_choices", None) != keys:
            screen._tpm_theme_choices = keys
            screen._layout_cache = screen._chrome_cache = None
            screen._color_pack_menu_rects = []
            screen._color_pack_menu_cursor = min(getattr(screen, "_color_pack_menu_cursor", 0), len(keys) - 1)
            if getattr(screen, "_color_pack_pending", None) not in keys:
                screen._color_pack_pending = None
                screen._color_pack_confirm_rects = {}

    def close(self):
        self._main()
        self.loading_complete = True
        self._drop_placeholder()
        self._remove_labels()
        self._restore()
        self.records = []
        self.state, self.detail = "idle", ""
