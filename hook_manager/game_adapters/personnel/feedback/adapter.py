# SPDX-License-Identifier: MPL-2.0
"""Attach optional feedback when native negotiation screens import normally."""

from functools import update_wrapper
import threading
from weakref import WeakKeyDictionary

from ....callables import CallableTarget
from ....manager import Manager
from .bindings import feedback_bindings
from .notice import PersonnelNotice


class PersonnelFeedback(Manager):
    def __init__(self, guards, state, report):
        super().__init__(lambda *args: None)
        self.guards, self.state, self.report = guards, state, report
        self.notices, self.ui = WeakKeyDictionary(), {}
        # UI groups have their own lifecycle: a changed screen can disable its
        # feedback while contract guards stay active. Observe ordinary imports.
        for kind in guards.planners:
            module = "ui.screens." + kind + "_negotiation_screen"
            self.observe(module, lambda module, kind=kind: self._ready(kind, module))

    def _report(self, error):
        super()._report(error)
        self.report("REGULATIONS UI COMPATIBILITY FAILED personnel: " + " ".join(str(error).split())[:512]
                    + "; feedback integration skipped; signing guards remain active")

    def _ready(self, kind, module):
        screen = getattr(module, kind.title() + "NegotiationScreen")
        self.ui[kind] = feedback_bindings(screen, kind)
        targets = [CallableTarget("personnel.ui." + kind + "." + role, module.__name__, (screen.__name__,), method, signature)
                   for role, method, signature in (("event", "handle_event", "(self, event)"),
                                                    ("draw", "draw", "(self, surface)"))]
        for target in targets:
            self.approve(target)
        self.install("personnel.ui." + kind, [{"target": target.name, "callback": "internal", "phase": "before"}
                                              for target in targets])
        return True

    def refuse(self, screen, message):
        if screen in self.notices:
            self.notices[screen].refuse(message)

    def _wrapper(self, name, binding, target, original, signature):
        kind, role = target.name.split(".")[-2:]
        group = self.groups[name]

        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            screen = bound.arguments["self"]
            # Cached bound methods can survive restoration; their disabled group
            # must still bypass presentation and avoid repeated diagnostics.
            if (group["state"] != "active" or self.closed or self.guards.closed or not self.guards.owners
                    or threading.current_thread() is not threading.main_thread()
                    or getattr(screen, "state", None) is not self.state):
                return original(*args, **kwargs)
            notice = self.notices.get(screen)
            if notice is None:
                try:
                    notice = self.notices[screen] = PersonnelNotice(self.ui[kind], kind)
                except Exception as error:
                    self._disable(name, error)
                    return original(*args, **kwargs)
            if role == "draw":
                # Distinguish native exceptions from our drawing failures. Before
                # native starts we can fall back; after it returns we keep that
                # frame. A native error propagates, and native is never retried.
                started, completed, result = False, False, None

                def native():
                    nonlocal started, completed, result
                    started = True
                    result = original(*args, **kwargs)
                    completed = True
                    return result

                try:
                    return notice.draw(screen, bound.arguments["surface"], native)
                except Exception as error:
                    if started and not completed:
                        raise
                    self._disable(name, error)
                    return result if completed else original(*args, **kwargs)
            try:
                if notice.handle_event(screen, bound.arguments["event"]):
                    return None
            except Exception as error:
                self._disable(name, error)
                return original(*args, **kwargs)
            previous = getattr(self.guards._local, "personnel_screen", None)
            # Only this player event associates a refusal with a screen. AI and
            # direct manager calls never create a player dialog as a side effect.
            self.guards._local.personnel_screen = screen
            try:
                return original(*args, **kwargs)
            finally:
                self.guards._local.personnel_screen = previous
                if notice.reason and screen.message != notice.reason:
                    notice.reason, notice.dialog.open = None, False
                    notice.inline.rect = None

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
