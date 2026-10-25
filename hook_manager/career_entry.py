# SPDX-License-Identifier: MPL-2.0
"""Hold career input and updates while trusted regulation setup is pending."""

from functools import update_wrapper
import inspect
import sys
import weakref

if __package__:
    from .callables import CallableTarget
    from .manager import HookError, Manager


class CareerEntryGate(Manager):
    """Keep menus running, but admit only the career that setup has approved.

    This gate does not wait on a socket or run mod code. It skips the displayed
    career's input/update calls until activation releases that exact object.
    Drawing and the game's main loop remain native and running.
    Loading saved records and screen construction happen before this boundary.
    """

    def __init__(self, discovery):
        super().__init__(lambda record: None)
        self._allowed = None
        try:
            self._main()
            screen_manager = discovery._screen_manager()
            self._game_type = getattr(sys.modules.get("game_state"), "GameState", None)
            if screen_manager is None or not isinstance(self._game_type, type):
                raise HookError("Career entry needs the running native screen manager")
            self._screen_manager = weakref.ref(screen_manager)
            targets = tuple(CallableTarget("career.entry." + method, "ui.screen_manager", ("ScreenManager",), method, shape)
                            for method, shape in (("handle_event", "(self, event)"), ("update", "(self)")))
            for target in targets:
                resolved = target.resolve({})
                if resolved is None or inspect.getattr_static(screen_manager, target.attribute) is not resolved[2]:
                    raise HookError("Career entry needs the mapped native screen methods")
                self.approve(target)
            self.install("career.entry", [{"target": target.name, "callback": "internal", "phase": "before"}
                                          for target in targets])
            if self.groups["career.entry"]["state"] != "active":
                raise HookError("Career entry wrappers did not install together")
        except BaseException:
            self.stop()
            raise

    def allow(self, career):
        """Release only a still-displayed career with both owned wrappers intact."""
        self._main()
        manager = self._screen_manager()
        group = self.groups.get("career.entry")
        if (self.closed or manager is None or getattr(manager.current, "state", None) is not career
                or not isinstance(career, self._game_type) or group is None or group["state"] != "active"
                or any(vars(owner).get(attribute) is not wrapper
                       for owner, attribute, _, wrapper, _ in group["changes"])):
            raise HookError("Career entry no longer owns the displayed career")
        self._allowed = weakref.ref(career)

    def _wrapper(self, name, binding, target, original, signature):
        group = self.groups[name]

        def wrapped(manager, *args, **kwargs):
            if group["state"] == "active" and manager is self._screen_manager():
                career = getattr(getattr(manager, "current", None), "state", None)
                allowed = self._allowed() if self._allowed else None
                # A new career object never inherits another save's permission.
                # Menus/loading screens with no GameState keep their native flow.
                if isinstance(career, self._game_type) and career is not allowed:
                    signature.bind(manager, *args, **kwargs)
                    return None
            return original(manager, *args, **kwargs)

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
