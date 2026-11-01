# SPDX-License-Identifier: MPL-2.0
"""Observe completed native imports and career navigation without loading either."""

from functools import update_wrapper
import re
import sys
import threading
from types import ModuleType
import weakref

if __package__:
    from .callables import CallableTarget
    from .manager import HookError, Manager


class NativeReadiness(Manager):
    """Share one navigation wrapper with career loading; retain no career strongly."""

    def __init__(self):
        super().__init__(lambda record: None)
        self.modules, self.career_requested = (), False
        self._listeners, self._career, self._manager = set(), None, None
        self.generation = 0
        self._loaded = set()
        self._activity_key, self._activity_sequence, self._activity_job = None, 0, None
        self._activity_status = {"kind": "game_loading", "sequence": 0, "generation": 0}

    def current_activity(self):
        """Return cached plain context safely to diagnostic builders on workers."""
        return dict(self._activity_status)

    def observe_screen(self, screen):
        """Seed navigation from the existing menu, before any later switch occurs."""
        if self.closed or not self.navigation_intact:
            return
        try:
            manager = getattr(screen, "manager", None)
            if manager is not None:
                self._observe(manager, screen)
                self.activity_snapshot()
        except Exception as error:
            self.store_error(error)  # Diagnostics must never break native drawing/input.

    def activity_snapshot(self):
        """Describe only confirmed native state; retain neither screen nor job."""
        manager = self._manager() if self._manager is not None else None
        kind, job = "game_loading", None
        navigation_state = self.groups.get("readiness.navigation", {}).get("state", "pending")
        # An ordinary deferred import is still startup. Lost ownership of an
        # installed observer means the current native activity is no longer known.
        if self.closed or self.errors or navigation_state != "pending" and not self.navigation_intact:
            kind = "unknown"
        elif self.navigation_intact and manager is not None:
            screen = getattr(manager, "current", None)
            self._observe(manager, screen)
            entry = tuple(value for name, label in (("hub_screen", "HubScreen"),
                ("load_game_screen", "LoadGameScreen"), ("team_select_screen", "TeamSelectScreen"))
                if isinstance(value := getattr(sys.modules.get("ui.screens." + name), label, None), type))
            job = getattr(screen, "_preload_job", None) if isinstance(screen, entry) else None
            if job is not None and not getattr(job, "cancelled", False) and not getattr(screen, "_shutting_down", False):
                kind = "save_loading"
            elif self._career is not None and self._career() is not None:
                kind = "in_save"
            elif screen is None or isinstance(screen, entry):
                kind = "menu"
            else:
                kind = "unknown"
        key = kind, self.generation
        previous_job = self._activity_job() if self._activity_job is not None else None
        if key != self._activity_key or kind == "save_loading" and job is not previous_job:
            self._activity_key = key
            self._activity_sequence += 1
        # Native jobs already support weak references for career loading. Compare
        # live identity so reuse of an abandoned job's address cannot hide a retry.
        self._activity_job = weakref.ref(job) if kind == "save_loading" else None
        self._activity_status = {"kind": kind, "sequence": self._activity_sequence, "generation": self.generation}
        return self.current_activity()

    def configure(self, request):
        """Accept bounded names to observe, never imports or executable callbacks."""
        if (type(request) is not dict or set(request) != {"career", "modules"}
                or type(request["career"]) is not bool or type(request["modules"]) is not list
                or len(request["modules"]) > 256
                or any(type(name) is not str or len(name) > 128
                       or not re.fullmatch(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", name, re.ASCII)
                       for name in request["modules"])):
            raise HookError("Invalid native prerequisite request")
        self.modules, self.career_requested = tuple(sorted(set(request["modules"]))), request["career"]
        self._loaded.intersection_update(self.modules)
        if self.career_requested:
            self.watch_career()

    def watch_career(self, callback=None):
        """Use the same native transition for availability and loading cancellation."""
        if not self.groups:
            target = CallableTarget("readiness.navigation", "ui.screen_manager", ("ScreenManager",),
                                    "switch_to", "(self, screen)")
            self.approve(target)
            self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "after"}])
        if callback is not None:
            self._listeners.add(callback)

    def unwatch_career(self, callback):
        self._listeners.discard(callback)

    @property
    def navigation_intact(self):
        return self.group_intact("readiness.navigation")

    def snapshot(self):
        """Return plain availability/activity; module, screen and career objects stay native."""
        if self.closed:
            return {"career": None, "modules": [], "watching": False, "detail": "", "activity": self.activity_snapshot()}
        self.poll()
        activity = self.activity_snapshot()
        # A module enters sys.modules before its import finishes. Remember only
        # completed imports, as milestones for this process.
        self._loaded.update(name for name in self.modules if type(module := sys.modules.get(name)) is ModuleType
                            and not getattr(vars(module).get("__spec__"), "_initializing", False))
        loaded = sorted(self._loaded)
        career = self._career() if (self._career is not None and self.navigation_intact
                                   and self._manager is not None and self._manager() is not None) else None
        return {"career": self.generation if career is not None else None, "modules": loaded,
                "watching": self.career_requested or len(loaded) != len(self.modules) or bool(self.groups) and not self.errors,
                "detail": self.errors[-1] if self.errors else "", "activity": activity}

    def _observe(self, manager, screen):
        if getattr(manager, "current", None) is not screen:
            return
        owner = self._manager() if self._manager is not None else None
        if owner is not None and owner is not manager:
            # Auxiliary screen managers must not replace the foreground career.
            return
        self._manager = weakref.ref(manager)
        entry_types = tuple(kind for name, label in (("hub_screen", "HubScreen"),
            ("load_game_screen", "LoadGameScreen"), ("team_select_screen", "TeamSelectScreen"))
            if isinstance(kind := getattr(sys.modules.get("ui.screens." + name), label, None), type))
        state = getattr(screen, "state", None)
        game = getattr(sys.modules.get("game_state"), "GameState", None)
        if screen is None or isinstance(screen, entry_types):
            # These screens may already hold a GameState while still loading it.
            state = None
        elif not isinstance(game, type) or not isinstance(state, game):
            return  # Options and other overlays do not end the current career.
        current = self._career() if self._career is not None else None
        if current is not state:
            # Distinguish successive careers even if neither update is empty.
            self.generation += 1
            self._career = weakref.ref(state) if state is not None else None

    def _wrapper(self, name, binding, target, original, signature):
        observer = self
        def navigate(self, screen):
            result = original(self, screen)
            if threading.current_thread() is threading.main_thread() and observer.navigation_intact:
                try:
                    observer._observe(self, screen)
                    observer.activity_snapshot()
                    for callback in tuple(observer._listeners):
                        callback(self, screen)
                except Exception as error:
                    observer._disable(name, error)
            return result
        update_wrapper(navigate, original)
        navigate._tpm_hook_group = name
        return navigate

    def stop(self):
        """Release listeners and weak references, then restore owned navigation."""
        self._listeners.clear()
        self._career = self._manager = None
        super().stop()
