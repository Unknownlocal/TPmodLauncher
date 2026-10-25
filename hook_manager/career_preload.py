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
    """

    _generations = count(1)  # Keep attempt numbers unique across stop/reinstall.

    def __init__(self):
        super().__init__(lambda record: None)
        self.generation, self._pending, self._allowed = 0, None, False
        try:
            helper = CallableTarget("career.preload.helper", "ui.session_preload", (),
                                    "advance_session_preload", "(screen, state)")
            resolved = helper.resolve({})
            if resolved is None:
                raise HookError("Career preloading needs the loaded native helper")
            self._native = resolved[2]
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
        if pending is not None and type(generation) is int and generation == self.generation:
            screen, state, job = (reference() for reference in pending)
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
        return {"state": "released" if self._allowed else "waiting", "generation": self.generation}

    def poll(self):
        """Finish normal imports on the main thread; a failed alias restores the set."""
        if not self.closed:
            super().poll()
            if self.errors:
                self.stop()
        return self.status()

    def _wrapper(self, name, binding, target, original, signature):
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
                if pending is None or any(reference() is not value for reference, value in zip(pending, (screen, state, job))):
                    # Record once per job. Ordinary frames check local state;
                    # they neither send IPC nor copy rules nor restart setup.
                    self.generation = next(self._generations)
                    self._pending = weakref.ref(screen), weakref.ref(state), weakref.ref(job)
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
