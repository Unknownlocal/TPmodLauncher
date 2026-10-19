# SPDX-License-Identifier: MPL-2.0
"""Controller-owned publication and dispatch; existing socket callers stay legacy."""

import math
import threading
import time

from mod_runtime._regulations.values import integer
from ..handler_runner import HandlerRunner
from .action import PreparedAction
from .catalog import CombinedCatalog


class ValidationDispatch:
    def __init__(self, definitions, handlers, *, epoch=1):
        self.epoch = integer(epoch, 64, positive=True)
        self.catalog = CombinedCatalog(definitions)
        if type(handlers) is not dict or handlers.keys() != self.catalog.owners.keys():
            raise ValueError("Every required resident owner needs its admitted handler")
        self.runners = {}
        for owner in self.catalog.owners:
            handler = handlers[owner]
            if type(handler) is not HandlerRunner or handler.runner.manifest.id != owner:
                raise ValueError("Foreign resident handler owner")
            self.runners[owner] = handler.validation()
        # Record/action IDs belong to one controller lifetime. Two dispatchers
        # must never attach independent catalogs to the same resident Store.
        if any(runner._claimed for runner in self.runners.values()):
            raise ValueError("Resident handler already belongs to a dispatcher")
        for runner in self.runners.values():
            runner._claimed = True
        self.states = dict.fromkeys(handlers, "pending")
        self.generation, self.action = 0, 0
        self._thread, self._busy = threading.get_ident(), False

    def _enter(self):
        if threading.get_ident() != self._thread or self._busy:
            raise RuntimeError("Resident dispatch requires its idle owning controller thread")
        self._busy = True

    def _fail(self, owners):
        for owner in owners:
            self.states[owner] = "unavailable"
            self.runners[owner].handler.runner.close()

    @property
    def ready(self):
        return all(state == "active" and not self.runners[owner].handler.runner.closed
                   for owner, state in self.states.items())

    def publish(self, *, native=None, custom=None):
        self._enter()
        affected = set(self.runners)
        try:
            started = time.monotonic()
            native, custom = {} if native is None else native, {} if custom is None else custom
            affected = self.catalog.affected(native, custom)
            if any(self.states[owner] == "unavailable" or self.runners[owner].handler.runner.closed for owner in affected):
                raise RuntimeError("A required resident owner is unavailable")
            self.generation = integer(self.generation + 1, 64, positive=True)
            deadline = started + 1 + 5 * len(affected)
            for owner in affected:
                self.states[owner] = "dirty"
                self.runners[owner].required = self.generation
            views = self.catalog.stage(native, custom)
            # Every affected Store is sealed before any is committed. Partial
            # commit failure closes all affected owners; no old view authorizes it.
            for owner, view in views.items():
                self.runners[owner].stage(view, self.epoch, self.generation, deadline)
            for owner in views:
                self.runners[owner].commit(self.epoch, self.generation, deadline)
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
            self._fail(affected)
            raise
        finally:
            self._busy = False

    def evaluate(self, contexts, selections, calls, *, deadline=None):
        self._enter()
        owners = set()
        try:
            started = time.monotonic()
            if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise ValueError("Invalid resident action deadline")
            deadline = min(started + .1, deadline) if deadline is not None else started + .1
            if type(calls) is not dict or not calls or not calls.keys() <= self.runners.keys():
                raise ValueError("Invalid resident action owners")
            owners = set(calls)
            if any(self.states[owner] != "active" or self.runners[owner].handler.runner.closed for owner in owners):
                raise RuntimeError("A required resident owner is unavailable")
            frame = PreparedAction(contexts, selections)
            packets = {owner: frame.for_owner(self.catalog.views[owner], calls[owner],
                                             f"{self.catalog.views[owner].revision}:{self.epoch}:{self.runners[owner].generation}")
                       for owner in self.runners if owner in calls}
            frame.seal()
            self.action = integer(self.action + 1, 64, positive=True)
            # Prepare every owner's envelope before any author function runs.
            # One original deadline covers preparation and every participating Store.
            owners, results = set(), {}
            # Preflight failures above invalidate the requested cohort; execution
            # failures below identify one owner and leave other Stores available.
            for owner, packet in packets.items():
                try:
                    results[owner] = self.runners[owner].evaluate(packet, self.action, deadline)
                except BaseException:
                    self._fail((owner,))
                    raise
            return results
        except BaseException:
            self._fail(owners)
            raise
        finally:
            self._busy = False

    def reset(self, epoch):
        self._enter()
        try:
            integer(epoch, 64, positive=True)
            if epoch <= self.epoch:
                raise ValueError("Resident career epoch must increase")
            deadline = time.monotonic() + 1 + 5 * len(self.runners)
            for owner, runner in self.runners.items():
                if self.states[owner] == "unavailable" or runner.handler.runner.closed:
                    raise RuntimeError("Reset cannot revive a failed resident owner")
                self.states[owner] = "dirty"
                runner.reset(epoch, deadline)
            self.catalog.reset()
            # Releasing the combined host graph also consumes the reset deadline,
            # even though every guest has already cleared its own view.
            if time.monotonic() >= deadline:
                raise RuntimeError("Late resident career reset")
            self.epoch = epoch
            self.states = dict.fromkeys(self.runners, "pending")
        except BaseException:
            self._fail(self.runners)
            raise
        finally:
            self._busy = False
