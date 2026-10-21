# SPDX-License-Identifier: MPL-2.0
"""Keep AI preparation private and retain player refusal feedback."""

import threading
import time
from weakref import WeakKeyDictionary

from ...manager import HookError
from .feedback import draw_refusal
from .preparation import AcademyPreparation


class AcademyCallers:
    def __init__(self, guards, state, methods):
        self.guards, self.state, self.methods = guards, state, methods
        # Native feedback plays a sound but drops the refusal reason. Retain it
        # outside native screen attributes, without keeping closed screens alive.
        self.notices = WeakKeyDictionary()

    def wrap(self, role, original, signature):
        def wrapped(*args, **kwargs):
            guards = self.guards
            if guards.closed or not guards.owners:
                return original(*args, **kwargs)
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            owner = bound.arguments["self"]
            if getattr(owner, "state", None) is not self.state:
                return original(*args, **kwargs)
            if role == "draw":
                result = original(*args, **kwargs)
                notice = self.notices.get(owner)
                if notice and notice[1] > time.monotonic():
                    try:
                        draw_refusal(owner, bound.arguments["surface"], notice[0])
                    except Exception as error:
                        guards._report(error)
                        self.notices.pop(owner, None)
                elif notice:
                    self.notices.pop(owner, None)
                return result
            if threading.current_thread() is not threading.main_thread() and not getattr(guards._local, "operation", False):
                # Reject before creating or publishing drafts. Trusted workers
                # must enter the same enrolled operation scope as direct guards.
                guards._report(HookError("Academy caller operation is not enrolled"))
                return None
            previous = getattr(guards._local, "academy_caller", None)
            frame = {"initiator": "player" if role == "ui" else "ai", "refusal": None,
                     "attempted": False, "preparation": None}
            guards._local.academy_caller = frame
            # Freeze subscriptions for the whole caller, including AI preparation
            # before its first guarded attempt and final publication afterward.
            with guards._lock:
                guards._active += 1
            try:
                if role != "ui":
                    preparation = AcademyPreparation(self.state, self.methods, original)
                    frame["preparation"] = preparation
                    # Run only the native caller on its view. Each actual signing
                    # attempt still crosses the real manager's live guard.
                    preparation.manager.sign_driver = self.state.academy_manager.sign_driver
                    preparation.manager.renew_contract = self.state.academy_manager.renew_contract
                    bound.arguments["self"] = preparation.manager
                return original(*bound.args, **bound.kwargs)
            finally:
                try:
                    if frame["preparation"] is not None:
                        frame["preparation"].finish()
                    elif frame["refusal"]:
                        self.notices[owner] = frame["refusal"], time.monotonic() + 3.0
                    elif frame["attempted"]:
                        # Unrelated mouse/key events must not erase a refusal.
                        self.notices.pop(owner, None)
                finally:
                    guards._local.academy_caller = previous
                    with guards._lock:
                        guards._active -= 1
        return wrapped
