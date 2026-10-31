# SPDX-License-Identifier: MPL-2.0
"""Attach optional personnel feedback when native screens import normally."""

from ....diagnostics import build_log_record

from functools import update_wrapper
import threading
from weakref import WeakKeyDictionary

from ....callables import CallableTarget
from ....manager import Manager
from .bindings import feedback_bindings
from .negotiation_notice import NegotiationNotice
from .inbox_notice import InboxNotice
from ...ui import ui_bindings


class PersonnelFeedback(Manager):
    def __init__(self, guards, state, submit_log, *, kinds=None):
        super().__init__(lambda *args: None)
        self.guards, self.state, self.submit_log = guards, state, submit_log
        self.notices, self.ui = WeakKeyDictionary(), {}
        # UI groups have their own lifecycle: a changed screen can disable its
        # feedback while contract guards stay active. Observe ordinary imports.
        for kind in guards.planners if kinds is None else kinds:
            module = "ui.screens." + kind + "_negotiation_screen"
            self.observe(module, lambda module, kind=kind: self._ready(kind, module))
        if kinds is None:
            # Only contract feedback owns inbox approvals; approach checks do
            # not sign agreements or require a mailbox integration.
            self.observe("ui.screens.mailbox_screen", lambda module: self._ready("inbox", module))

    def store_error(self, error):
        """Retain a feedback failure and submit its compatibility warning."""
        super().store_error(error)
        self.submit_log(build_log_record('WARNING', "REGULATIONS UI COMPATIBILITY FAILED personnel: " + str(error)
                    + "; feedback integration skipped; signing guards remain active", error=error, component="regulations"))

    def _ready(self, kind, module):
        screen = getattr(module, "MailboxScreen" if kind == "inbox" else kind.title() + "NegotiationScreen")
        self.ui[kind] = ui_bindings(screen) if kind == "inbox" else feedback_bindings(screen, kind)
        methods = [("event", "handle_event", "(self, event)"), ("draw", "draw", "(self, surface)")]
        if kind == "inbox":
            methods.append(("execute", "_execute_action", "(self, action: Dict) -> bool"))
        elif kind == "staff":
            methods.append(("approach", "_confirm_begin_approach", "(self)"))
        else:
            methods.append(("approach_failure", "_show_approach_failure",
                            "(self, reason: str, *, title='Approach cannot begin', note='No approach was started or personnel slot used.') -> None"))
        targets = [CallableTarget("personnel.ui." + kind + "." + role, module.__name__, (screen.__name__,), method, signature)
                   for role, method, signature in methods]
        for target in targets:
            self.approve(target)
        self.install("personnel.ui." + kind, [{"target": target.name, "callback": "internal", "phase": "before"}
                                              for target in targets])
        return True

    def refuse(self, screen, message):
        notice = self.notices.get(screen)
        if notice is not None:
            try:
                notice.refuse(message)
            except Exception as error:
                # Presentation failure must not replace the signing refusal.
                self._disable("personnel.ui." + notice.kind, error)

    def _disable(self, name, error=None):
        """Restore a screen's hooks and discard its retained presentation state."""
        super()._disable(name, error)
        for screen, notice in tuple(self.notices.items()):
            if name == "personnel.ui." + notice.kind:
                del self.notices[screen]

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
            if role == "approach_failure":
                # Keep the native result even if optional feedback fails.
                result = original(*args, **kwargs)
                try:
                    screen.action_feedback(False)
                except Exception as error:
                    self._disable(name, error)
                return result
            notice = self.notices.get(screen)
            if notice is None:
                try:
                    notice = self.notices[screen] = (InboxNotice(self.ui[kind]) if kind == "inbox"
                                                   else NegotiationNotice(self.ui[kind], kind))
                except Exception as error:
                    self._disable(name, error)
                    return original(*args, **kwargs)
            if role in ("draw", "approach", "execute"):
                # Distinguish native exceptions from presentation failures. Before
                # native starts we can fall back; after it returns we keep its
                # result. A native error propagates and native is never retried.
                started, completed, result = False, False, None

                def native():
                    nonlocal started, completed, result
                    started = True
                    result = original(*args, **kwargs)
                    completed = True
                    return result

                try:
                    if role == "draw":
                        return notice.draw(screen, bound.arguments["surface"], native)
                    if role == "execute":
                        return notice.execute(screen, bound.arguments["action"], native, self.guards._local)
                    return notice.confirm_approach(screen, native)
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
            if kind == "inbox":
                # The action wrapper scopes the particular approval. The whole
                # inbox event can contain unrelated actions and has no message field.
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
