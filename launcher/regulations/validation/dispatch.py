# SPDX-License-Identifier: MPL-2.0
"""Controller-owned publication and dispatch for the private regulation protocol."""

import math
import threading
import time

from mod_runtime._regulations.values import integer
from ..regulation_handler_bindings import RegulationHandlerBindings
from .action import PreparedAction
from .catalog import CombinedCatalog
from .group import ValidationGroup


class ValidationDispatch:
    """Publish rules and run ordered mod checks through one shared interpreter."""

    def __init__(self, definitions, handler=None, *, epoch=1):
        self.epoch = integer(epoch, 64, positive=True)
        self.catalog = CombinedCatalog(definitions)
        self.handler = handler
        self.group = None
        if self.catalog.owners or definitions.voting or handler is not None:
            if (type(handler) is not RegulationHandlerBindings
                    or getattr(handler.runner, "_regulation_handlers", None) is not handler
                    or handler.validators.keys() != self.catalog.owners.keys()):
                raise ValueError("Every regulation owner must use one admitted shared handler")
            # One runner has one record cache and action sequence. A second
            # dispatcher must not attach a competing catalogue to that state.
            if handler._claimed:
                raise ValueError("Resident handler already belongs to a dispatcher")
            if not self.catalog.owners and not handler.voting:
                raise ValueError("Definition-only rules need no execution handler")
            if self.catalog.owners:
                self.group = ValidationGroup(handler)
            handler._claimed = True
        self.states = dict.fromkeys((*self.catalog.owners, *definitions.voting), "pending")
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
        if self.handler is not None:
            self.handler.runner.close()

    @property
    def ready(self):
        return (self.catalog.native_values_ready
                and all(state == "active" for state in self.states.values())
                and (self.handler is None or not self.handler.runner.closed))

    def native_rules(self, *, epoch, generation):
        """Prepare fixed switches for native setup, using this dispatcher's identity.

        The game retains booleans or owned option references under its policy
        acknowledgement. Native queries must not make a transport call here.
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
            self.catalog.check_publication(native, custom)
            affected = self.states
            if any(state == "unavailable" for state in self.states.values()) or (self.handler is not None and self.handler.runner.closed):
                raise RuntimeError("A required resident owner is unavailable")
            self.generation = integer(self.generation + 1, 64, positive=True)
            limit = started + 1 + 5 * len(affected)
            # Native plan/page transfer has already spent part of this budget;
            # entering guest installation must not grant another full allowance.
            deadline = limit if deadline is None else min(limit, deadline)
            for owner in affected:
                self.states[owner] = "dirty"
            views = self.catalog.stage(native, custom)
            if views:
                self.group.stage(views, self.epoch, self.generation, deadline)
                self.group.commit(self.epoch, self.generation, deadline)
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident publication")
            self.catalog.commit()
            # Host index retirement is still publication work. Guest commits
            # succeeding cannot turn late host completion into an acknowledgement.
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident publication")
            for owner in affected:
                self.states[owner] = "active"
            return dict.fromkeys(self.states, self.generation)
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
            if type(calls) is not dict or not calls or not calls.keys() <= self.states.keys():
                raise ValueError("Invalid resident action owners")
            if self.group.handler.runner.closed or any(self.states[owner] != "active" for owner in calls):
                raise RuntimeError("A required resident owner is unavailable")
            frame = PreparedAction(contexts, selections)
            packets = {owner: frame.for_owner(self.catalog.views[owner], calls[owner],
                                             f"{self.catalog.views[owner].revision}:{self.epoch}:{self.generation}")
                       for owner in self.states if owner in calls}
            shared = frame.for_group()
            frame.seal()
            self.action = integer(self.action + 1, 64, positive=True)
            # Preflight every packet before author code runs. Transfer the shared
            # proposal once, execute in owner order, then finish before replying.
            self.group.begin_action(shared, self.action, deadline)
            results = {owner: self.group.evaluate(owner, packet, self.action, deadline)
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
            limit = time.monotonic() + 1 + 5 * len(self.states)
            deadline = limit if deadline is None else min(limit, deadline)
            if any(state == "unavailable" for state in self.states.values()) or (self.handler is not None and self.handler.runner.closed):
                raise RuntimeError("Reset cannot revive a failed resident owner")
            self.states = dict.fromkeys(self.states, "dirty")
            if self.group is not None:
                self.group.reset(epoch, deadline)
            self.catalog.reset()
            # Releasing the combined host graph also consumes the reset deadline,
            # even though every guest has already cleared its own view.
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident career reset")
            self.epoch = epoch
            self.states = dict.fromkeys(self.states, "pending")
        except BaseException:
            self._fail()
            raise
        finally:
            self._busy = False
