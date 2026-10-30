# SPDX-License-Identifier: MPL-2.0
"""Add enabled mods' prepared categories at the game's definition-loading point."""

from copy import deepcopy
from functools import update_wrapper
from pathlib import Path

from ...callables import CallableTarget
from ...manager import HookError, Manager
from .regulation_scripts import RegulationScripts


class RegulationCategoryAdapter(Manager):
    """Keep category loading with the pack, before any career chooses its rules.

    The launcher supplies already checked category data, never a callable or a
    game target. Native file reading, built-in defaults and saved selections
    remain native. Each load receives its own copy of the added definitions.
    """

    def __init__(self, request, regulation_changes):
        super().__init__(lambda record: None)
        # Bundle cleanup shares this reservation with regulation change loads and saves.
        # Native work runs outside the lock, so workers can do normal file I/O.
        regulation_changes.check()
        if regulation_changes.categories is not None:
            raise HookError("Regulation change adapter already owns a category loader")
        self.regulation_changes, self._lock = regulation_changes, regulation_changes._lock
        self.scripts, self.automatic = None, None
        if (type(request) is not dict or set(request) - {"pack", "categories", "scripted_changes", "automatic_changes"}
                or not {"pack", "categories"} <= request.keys()
                or type(request["pack"]) is not str or not Path(request["pack"]).is_absolute()
                or type(request["categories"]) is not list or not 1 <= len(request["categories"]) <= 128
                or type(request.get("scripted_changes", [])) is not list
                or type(request.get("automatic_changes", {})) is not dict):
            raise HookError("Expected prepared categories and their absolute pack folder")
        self.pack = Path(request["pack"]).resolve(strict=True)
        self.categories = {row["key"]: deepcopy(row) for row in request["categories"]}
        if len(self.categories) != len(request["categories"]):
            raise HookError("Prepared categories contain duplicate keys")
        try:
            # Return annotations are part of the native method's checked signature.
            target = CallableTarget("regulations.categories", "managers.regulation_manager",
                                    ("RegulationManager",), "_load_regulation_defs", "(self) -> 'Dict[str, Dict[str, Any]]'")
            # Install before releasing career selection. A deferred worker import
            # could otherwise finish loading a career before a main-thread poll.
            if target.resolve({}) is None:
                raise HookError("Category loading needs the loaded native regulation manager")
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "after"}])
            if request.get("scripted_changes"):
                # Scripts can target only owned categories, so their loading
                # helper shares this pack's lifetime and cleanup reservation.
                self.scripts = RegulationScripts(self, request["scripted_changes"])
            regulation_changes.categories = self
            if request.get("automatic_changes"):
                regulation_changes.install_automatic(request["automatic_changes"])
        except BaseException:
            self.stop()
            raise

    def check(self, state):
        """Confirm this career loaded our categories before publishing its policy."""
        group = self.groups["regulations.categories"]
        if (self.closed or self.errors or group["state"] != "active"
                or any(vars(owner).get(attribute) is not wrapper
                       for owner, attribute, _, wrapper, _ in group["changes"])
                or Path(state.data_dir).resolve() != self.pack
                or any(state.regulation_manager.regulation_defs.get(key) != value
                       for key, value in self.categories.items())):
            raise HookError("The career did not load the prepared mod regulation categories")
        if self.scripts is not None:
            self.scripts.check(state)
        if self.automatic is not None:
            self.automatic.check(state)

    def merge_definitions(self, saved):
        """Add newly enabled categories without replacing differing saved rules."""
        definitions = dict(saved)
        for key, category in self.categories.items():
            if key in definitions and definitions[key] != category:
                raise HookError("Saved mod category differs from its prepared definition: " + key)
            if key not in definitions:
                definitions[key] = deepcopy(category)
        return definitions

    def stop(self):
        """Detach prepared declarations while retaining shared save/load handling."""
        if self.regulation_changes.categories is self:
            self.regulation_changes.categories = None
        return super().stop()

    @property
    def _active(self):
        """Share the load/save reservation used by bundle cleanup."""
        return self.regulation_changes._active

    def _wrapper(self, name, binding, target, original, signature):
        """Keep native loading first, then add this pack's checked category copies."""
        if name == "regulations.automatic":
            return self.regulation_changes._wrapper(name, binding, target, original, signature,
                                                    group=self.groups[name])
        group = self.groups[name]

        def wrapped(manager):
            with self._lock:
                if group["state"] != "active":
                    return original(manager)
                self.regulation_changes._active += 1
            try:
                definitions = original(manager)
                try:
                    state = manager.state
                    # This wrapper belongs to one Formula pack for its whole
                    # lifetime, even when another career is being constructed.
                    if (getattr(state, "game_mode", "formula") == "oval"
                            or Path(state.data_dir).resolve() != self.pack):
                        return definitions
                    if type(definitions) is not dict or definitions.keys() & self.categories.keys():
                        raise HookError("Mod regulation categories conflict with native definitions")
                    return definitions | deepcopy(self.categories)
                except Exception as error:
                    # Preserve a completed native load if our mapping fails.
                    # The subsequent setup check refuses to activate mod rules.
                    self._report(error)
                    return definitions
            finally:
                with self._lock:
                    self.regulation_changes._active -= 1

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
