# SPDX-License-Identifier: MPL-2.0
"""Extend native loading completion while a career's regulation setup is pending."""

from functools import update_wrapper
from itertools import count
import sys
import weakref

if __package__:
    from .callables import CallableTarget
    from .manager import HookError, Manager


class CareerPreload(Manager):
    """Keep the native loading screen until its exact loading attempt is approved.

    Native asset work, input, drawing and screen transitions stay in the game.
    Only its completion result is held. The three known screens import copies
    of the helper, so each fixed alias needs a wrapper, including later imports.
    One native navigation method also reports leaving the career; it never
    replaces the game's input, update, saving or screen-transition logic.
    """

    _generations = count(1)  # Keep attempt numbers unique across stop/reinstall.

    def __init__(self, leaving=lambda: None):
        super().__init__(lambda record: None)
        self.generation, self._pending, self._allowed = 0, None, False
        self._left, self._leaving = False, leaving
        try:
            helper = CallableTarget("career.preload.helper", "ui.session_preload", (),
                                    "advance_session_preload", "(screen, state)")
            resolved = helper.resolve({})
            if resolved is None:
                raise HookError("Career preloading needs the loaded native helper")
            self._native = resolved[2]
            navigation = CallableTarget("career.preload.navigation", "ui.screen_manager", ("ScreenManager",),
                                        "switch_to", "(self, screen)")
            self.approve(navigation)
            self.install(navigation.name, [{"target": navigation.name, "callback": "internal", "phase": "before"}])
            if self.groups[navigation.name]["state"] != "active":
                raise HookError("Career preloading needs native navigation")
            for screen in ("hub_screen", "load_game_screen", "team_select_screen"):
                target = CallableTarget("career.preload." + screen, "ui.screens." + screen, (),
                                        "advance_session_preload", helper.signature)
                self.approve(target)
                self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            # The hub exists before startup can release New/Continue/Load Game.
            # Other screen aliases may wait for their normal imports.
            if self.groups["career.preload.hub_screen"]["state"] != "active":
                raise HookError("Career preloading needs the native hub import")
        except BaseException:
            self.stop()
            raise

    def current(self, generation):
        """Reject a reply for an abandoned, replaced or cancelled loading job."""
        self._main()
        pending = self._pending
        if pending is not None and not self._left and type(generation) is int and generation == self.generation:
            screen, state, job, _ = (reference() for reference in pending)
            manager = getattr(screen, "manager", None)
            if (screen is not None and state is not None and job is not None
                    and getattr(manager, "current", None) is screen
                    and getattr(screen, "state", None) is state
                    and getattr(screen, "_preload_job", None) is job
                    and not getattr(screen, "_shutting_down", False)
                    and not getattr(job, "cancelled", False)):
                return state
        raise HookError("The requested regulation loading attempt is no longer current")

    def allow(self, career):
        """Release the same pending job only while all installed aliases remain owned."""
        self._main()
        if (self._pending is None or self.current(self.generation) is not career
                or any(row["state"] not in ("active", "pending") for row in self.groups.values())
                or any(vars(owner).get(attribute) is not wrapper
                       for row in self.groups.values() for owner, attribute, _, wrapper, _ in row["changes"])):
            raise HookError("Career preloading no longer owns the loading attempt")
        self._allowed = True

    def loading_status(self):
        """Return one attempt number, never its screen, job or career objects."""
        if self.errors:
            return {"state": "unavailable", "detail": self.errors[-1]}
        if self._pending is None:
            return {"state": "armed"}
        return {"state": "left" if self._left else "released" if self._allowed else "waiting", "generation": self.generation}

    def _depart(self):
        """Revoke approval locally before the controller retires the career's hooks."""
        if self._pending is not None and not self._left:
            self._left, self._allowed = True, False
            self._leaving()  # Trusted adapter callback; no IPC or mod execution.

    def poll(self):
        """Finish normal imports on the main thread; a failed alias restores the set."""
        if not self.closed:
            super().poll()
            if self.errors:
                self.stop()
            elif self._pending is not None and not self._allowed and not self._left:
                try:
                    self.current(self.generation)
                except HookError:
                    self._depart()
        return self.status()

    def _wrapper(self, name, binding, target, original, signature):
        if name == "career.preload.navigation":
            def navigate(manager, screen):
                """Observe completed native navigation without changing its result."""
                result = original(manager, screen)
                if self.groups[name]["state"] == "active" and self._pending is not None:
                    # The loading screen can be collected after approval, so
                    # retain its manager weakly to recognise this session's exit.
                    if self._pending[3]() is manager and manager.current is screen:
                        hub = getattr(sys.modules.get("ui.screens.hub_screen"), "HubScreen", None)
                        game = getattr(sys.modules.get("game_state"), "GameState", None)
                        state = getattr(screen, "state", None)
                        if (screen is None or isinstance(hub, type) and isinstance(screen, hub)
                                or isinstance(game, type) and isinstance(state, game) and state is not self._pending[1]()):
                            self._depart()
                return result
            update_wrapper(navigate, original)
            navigate._tpm_hook_group = name
            return navigate
        # Check copies against today's loaded helper, never a saved body hash.
        if original is not self._native:
            raise HookError("Native preload alias no longer uses the mapped helper")
        group = self.groups[name]

        def wrapped(screen, state):
            ready = original(screen, state)
            try:
                game_type = getattr(sys.modules.get("game_state"), "GameState", None)
                manager = getattr(screen, "manager", None)
                if (group["state"] != "active" or not ready or not isinstance(game_type, type)
                        or not isinstance(state, game_type) or getattr(manager, "series_mode", "formula") != "formula"
                        or getattr(manager, "current", None) is not screen or getattr(screen, "_shutting_down", False)):
                    return ready
                job = getattr(screen, "_preload_job", None)
                if job is None:
                    raise HookError("Native preloading completed without its loading job")
                pending = self._pending
                if self._left or pending is None or any(reference() is not value for reference, value in zip(pending, (screen, state, job, manager))):
                    # Record once per job. Ordinary frames check local state;
                    # they neither send IPC nor copy rules nor restart setup.
                    self._depart()
                    self.generation = next(self._generations)
                    self._pending = tuple(weakref.ref(value) for value in (screen, state, job, manager))
                    self._left = False
                    self._allowed = False
                    try:
                        screen._on_loading_status("Preparing regulation checks...", 0, 1)
                    except Exception:
                        # Native status updates are best-effort. Presentation
                        # failure must not grant approval or abort the load.
                        pass
                return ready if self._allowed else False
            except Exception as error:
                # Only our post-native mapping failed. Report/restore this
                # integration and retain native completion; never replay assets
                # or catch an exception from the original helper itself.
                self._report(error)
                self.stop()
                return ready

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
