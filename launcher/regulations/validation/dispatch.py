# SPDX-License-Identifier: MPL-2.0
"""Controller-owned publication and dispatch for the private regulation protocol."""

import math
import threading
import time

from mod_runtime._regulations.values import integer
from ..shared_handler_runner import SharedHandlerRunner
from .action import PreparedAction
from .catalog import CombinedCatalog
from .group import ValidationGroup


class ValidationDispatch:
    """Publish rules and run ordered mod checks through one shared interpreter."""

    def __init__(self, definitions, handler=None, *, epoch=1):
        self.epoch = integer(epoch, 64, positive=True)
        self.catalog = CombinedCatalog(definitions)
        self.group = None
        if self.catalog.owners:
            if (type(handler) is not SharedHandlerRunner
                    or getattr(handler.runner, "_regulation_handlers", None) is not handler
                    or handler.validators.keys() != self.catalog.owners.keys()):
                raise ValueError("Every regulation owner must use one admitted shared handler")
            # One runner has one record cache and action sequence. A second
            # dispatcher must not attach a competing catalogue to that state.
            if handler._claimed:
                raise ValueError("Resident handler already belongs to a dispatcher")
            self.group = ValidationGroup(handler)
            handler._claimed = True
        elif handler is not None:
            raise ValueError("Definition-only rules need no execution handler")
        self.runners = self.group.runners if self.group is not None else {}
        self.states = dict.fromkeys(self.runners, "pending")
        self.generation, self.action = 0, 0
        self._thread, self._busy = threading.get_ident(), False

    def _enter(self):
        if threading.get_ident() != self._thread or self._busy:
            raise RuntimeError("Resident dispatch requires its idle owning controller thread")
        self._busy = True

    def _fail(self):
        self.catalog.fail_native_values()
        # All mods share this interpreter, including peers omitted from a call.
        # A failed action or update cannot leave part of its state usable.
        self.states = dict.fromkeys(self.states, "unavailable")
        if self.group is not None:
            self.group.handler.runner.close()

    @property
    def ready(self):
        return (self.catalog.native_values_ready
                and all(state == "active" for state in self.states.values())
                and (self.group is None or not self.group.handler.runner.closed))

    def native_rules(self, *, epoch, generation):
        """Prepare fixed switches for native setup, using this dispatcher's identity.

        The game retains these booleans under its policy acknowledgement; queries
        must not make a transport call here. Custom conditions and seasonal data
        selection belong to ordinary validators, not to these native switches.
        """
        self._enter()
        try:
            integer(epoch, 64, positive=True)
            integer(generation, 64, positive=True)
            if (epoch, generation) != (self.epoch, self.generation):
                raise ValueError("Stale native rule policy")
            if not self.ready:
                raise RuntimeError("Native rule policy is unavailable")
            return self.catalog.native_rules()
        finally:
            self._busy = False

    def publish(self, *, native=None, custom=None, deadline=None):
        self._enter()
        try:
            started = time.monotonic()
            native, custom = {} if native is None else native, {} if custom is None else custom
            affected = self.catalog.affected(native, custom)
            if any(self.states[owner] == "unavailable" or self.runners[owner].handler.runner.closed for owner in affected):
                raise RuntimeError("A required resident owner is unavailable")
            self.generation = integer(self.generation + 1, 64, positive=True)
            limit = started + 1 + 5 * len(affected)
            # Native plan/page transfer has already spent part of this budget;
            # entering guest installation must not grant another full allowance.
            deadline = limit if deadline is None else min(limit, deadline)
            for owner in affected:
                self.states[owner] = "dirty"
                self.runners[owner].required = self.generation
            views = self.catalog.stage(native, custom)
            if views:
                self.group.begin_publication(tuple(views))
            # Seal every affected owner before committing any replacement.
            # A partial commit closes the runner; old views cannot authorise it.
            for owner, view in views.items():
                self.group.stage(owner, view, self.epoch, self.generation, deadline)
            for owner in views:
                self.runners[owner].commit(self.epoch, self.generation, deadline)
            if views:
                self.group.finish_publication(deadline)
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident publication")
            self.catalog.commit()
            # Host index retirement is still publication work. Guest commits
            # succeeding cannot turn late host completion into an acknowledgement.
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident publication")
            for owner in affected:
                self.states[owner] = "active"
            return {owner: self.runners[owner].generation for owner in views}
        except BaseException:
            self.catalog.abort()
            self._fail()
            raise
        finally:
            self._busy = False

    def evaluate(self, contexts, selections, calls, *, deadline=None):
        self._enter()
        try:
            started = time.monotonic()
            if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise ValueError("Invalid resident action deadline")
            deadline = min(started + .1, deadline) if deadline is not None else started + .1
            if type(calls) is not dict or not calls or not calls.keys() <= self.runners.keys():
                raise ValueError("Invalid resident action owners")
            if self.group.handler.runner.closed or any(self.states[owner] != "active" for owner in calls):
                raise RuntimeError("A required resident owner is unavailable")
            frame = PreparedAction(contexts, selections)
            references = {}
            packets = {owner: frame.for_owner(self.catalog.views[owner], calls[owner],
                                             f"{self.catalog.views[owner].revision}:{self.epoch}:{self.runners[owner].generation}",
                                             shared=references)
                       for owner in self.runners if owner in calls}
            shared = frame.for_group(references)
            frame.seal()
            self.action = integer(self.action + 1, 64, positive=True)
            # Preflight every packet before author code runs. Transfer the shared
            # proposals once, execute in owner order, then finish before replying.
            self.group.begin_action(shared, self.action, deadline)
            results = {owner: self.runners[owner].evaluate(packet, self.action, deadline)
                       for owner, packet in packets.items()}
            self.group.finish_action(self.action, deadline)
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident regulation action")
            return results
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False

    def reset(self, epoch, *, deadline=None):
        self._enter()
        try:
            integer(epoch, 64, positive=True)
            if epoch <= self.epoch:
                raise ValueError("Resident career epoch must increase")
            limit = time.monotonic() + 1 + 5 * len(self.runners)
            deadline = limit if deadline is None else min(limit, deadline)
            for owner, runner in self.runners.items():
                if self.states[owner] == "unavailable" or runner.handler.runner.closed:
                    raise RuntimeError("Reset cannot revive a failed resident owner")
                self.states[owner] = "dirty"
            if self.group is not None:
                self.group.reset(epoch, deadline)
            self.catalog.reset()
            # Releasing the combined host graph also consumes the reset deadline,
            # even though every guest has already cleared its own view.
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident career reset")
            self.epoch = epoch
            self.states = dict.fromkeys(self.runners, "pending")
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False
