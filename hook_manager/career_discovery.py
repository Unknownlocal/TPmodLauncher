# SPDX-License-Identifier: MPL-2.0
"""Find the career displayed by the native main loop without adding game hooks."""

import inspect
import sys
import threading
from types import ModuleType
import weakref

if __package__:
    from .callables import CallableTarget
    from .manager import HookError


class CareerDiscovery:
    """Read the active screen's career; keep game objects out of bridge replies.

    The game keeps its screen manager in main._run_game's local variables. We
    inspect only that running function, identifying its manager by native type
    rather than by a local variable name. No game function is called and no
    frame, screen or career is kept alive by discovery.
    """

    def __init__(self):
        self.state, self.generation, self.detail = "pending", 0, None
        self._career = None

    def status(self):
        """Copy status and the career number; never return live game objects."""
        result = {"state": self.state, "generation": self.generation}
        if self.detail:
            result["detail"] = self.detail
        return result

    def _remember(self, career):
        previous = self._career() if self._career else None
        # A dead weak reference still records an earlier career. Closing it
        # must invalidate old setup numbers even if the object is already gone.
        if career is not previous or self._career is not None and career is None:
            self.generation += 1
        self._career = weakref.ref(career) if career is not None else None

    @staticmethod
    def _screen_manager():
        main, ui = sys.modules.get("__main__"), sys.modules.get("ui.screen_manager")
        if (type(main) is not ModuleType or "_run_game" not in vars(main)
                or type(ui) is not ModuleType
                or getattr(getattr(ui, "__spec__", None), "_initializing", False)):
            return None  # Normal startup has not reached the native loop yet.
        target = CallableTarget("career.main-loop", "__main__", (), "_run_game", "(crash_reporter=None)")
        resolved = target.resolve({})
        if resolved is None:
            return None
        screen_type = vars(ui).get("ScreenManager")
        if not isinstance(screen_type, type):
            raise HookError("Native ScreenManager is unavailable")
        code = inspect.unwrap(resolved[2]).__code__
        frame = sys._getframe()
        try:
            while frame is not None:
                # This is the current function's identity, not a stored code
                # fingerprint. Compatible game updates use their new code here.
                if frame.f_code is code and frame.f_globals is vars(main):
                    # Several local names may refer to the same manager.
                    managers = {id(value): value for value in frame.f_locals.values() if isinstance(value, screen_type)}
                    if len(managers) > 1:
                        raise HookError("Native main loop has more than one screen manager")
                    return next(iter(managers.values()), None)
                frame = frame.f_back
            return None
        finally:
            # Retaining a frame would also retain everything in its career.
            del frame

    def poll(self):
        """Refresh on the main thread; unsupported mappings leave native play alone."""
        if threading.current_thread() is not threading.main_thread():
            raise HookError("Career discovery requires the Python main thread")
        if self.state in ("unavailable", "stopped"):
            return self.status()
        try:
            manager = self._screen_manager()
            candidate = getattr(getattr(manager, "current", None), "state", None)
            game = sys.modules.get("game_state")
            career_type = getattr(game, "GameState", None)
            career = candidate if isinstance(career_type, type) and isinstance(candidate, career_type) else None
            self._remember(career)
            self.state = "pending" if manager is None else "loaded" if career is not None else "no_career"
        except Exception as error:
            self._remember(None)
            self.state = "unavailable"
            self.detail = ("REGULATIONS COMPATIBILITY FAILED career.discovery: "
                           + (" ".join(str(error).split())[:512] or type(error).__name__)
                           + "; career integration skipped; continuing native game")
        return self.status()

    def current(self, generation):
        """Reject setup for a career that changed since the controller saw it."""
        status = self.poll()
        if type(generation) is not int or generation != self.generation or status["state"] != "loaded":
            raise HookError("The requested career is no longer current")
        return self._career()

    def stop(self):
        """Forget the observed career when the bridge closes, leaving the game alone."""
        self._remember(None)
        self.state = "stopped"
