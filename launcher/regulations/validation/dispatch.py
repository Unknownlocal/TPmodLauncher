# SPDX-License-Identifier: MPL-2.0
"""Controller-owned publication and dispatch for the private regulation protocol."""

import math
import threading
import time

from mod_runtime._regulations.values import integer
from ..shared_handler_runner import SharedHandlerRunner
from .action import PreparedAction
from .catalog import CombinedCatalog


class ValidationDispatch:
    def __init__(self, definitions, handlers, *, epoch=1):
        self.epoch = integer(epoch, 64, positive=True)
        self.catalog = CombinedCatalog(definitions)
        if type(handlers) is not dict or handlers.keys() != self.catalog.owners.keys():
            raise ValueError("Every required resident owner needs its admitted handler")
        self.runners, self.groups = {}, {}
        for owner in self.catalog.owners:
            handler = handlers[owner]
            if (type(handler) is not SharedHandlerRunner
                    or getattr(handler.runner, "_regulation_handlers", None) is not handler
                    or owner not in handler.validators
                    or any(handlers.get(member) is not handler for member in handler.validators)):
                raise ValueError("Every shared handler owner must belong to this dispatcher")
            group = handler.validation()
            self.groups[owner] = group
            self.runners[owner] = group.runners[owner]
        # Record/action IDs belong to one controller lifetime. Two dispatchers
        # must never attach independent catalogs to the same resident Store.
        # Complete group admission makes its cached member claims cover the
        # physical shared Store as well as each owner's logical view.
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
        self.catalog.rookie.fail()
        # A shared Store cannot retain one healthy member after another fails.
        # Keep the original IDs in status, and close each physical runner once.
        affected = set(owners)
        for owner in tuple(affected):
            affected.update(self.groups[owner].runners)
        runners = set()
        for owner in affected:
            self.states[owner] = "unavailable"
            runners.add(self.runners[owner].handler.runner)
        for runner in runners:
            runner.close()

    @property
    def ready(self):
        return self.catalog.rookie.ready and all(state == "active" and not self.runners[owner].handler.runner.closed
                   for owner, state in self.states.items())

    def publish(self, *, native=None, custom=None, deadline=None):
        self._enter()
        # Until affected-rule resolution succeeds, publication failure cannot
        # safely identify a smaller cohort; all required owners remain fenced.
        affected = set(self.runners)
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
            views = self.catalog.stage(native, custom, epoch=self.epoch, generation=self.generation)
            groups = dict.fromkeys(self.groups[owner] for owner in views)
            for group in groups:
                group.begin_publication(tuple(owner for owner in views if self.groups[owner] is group))
            # Every affected Store is sealed before any is committed. Partial
            # commit failure closes all affected owners; no old view authorizes it.
            for owner, view in views.items():
                self.groups[owner].stage(owner, view, self.epoch, self.generation, deadline)
            for owner in views:
                self.runners[owner].commit(self.epoch, self.generation, deadline)
            for group in groups:
                group.finish_publication(deadline)
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
        owners, pending = set(), set()
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
            references = {self.groups[owner]: {} for owner in calls}
            packets = {owner: frame.for_owner(self.catalog.views[owner], calls[owner],
                                             f"{self.catalog.views[owner].revision}:{self.epoch}:{self.runners[owner].generation}",
                                             shared=references[self.groups[owner]])
                       for owner in self.runners if owner in calls}
            shared = {group: frame.for_group(rows) for group, rows in references.items()}
            remaining = {group: sum(self.groups[owner] is group for owner in packets) for group in shared}
            frame.seal()
            self.action = integer(self.action + 1, 64, positive=True)
            # Prepare every owner's envelope before any author function runs.
            # One original deadline covers preparation and every participating Store.
            # Clear the outer failure cohort after preflight. During execution,
            # only the failed Store and any still-open shared actions must close.
            owners, results = set(), {}
            # A group's members can straddle another group's owners in ID order.
            # Keep its table open from the first to the last requested member.
            for owner, packet in packets.items():
                group = self.groups[owner]
                try:
                    if group not in pending:
                        pending.add(group)
                        group.begin_action(shared[group], self.action, deadline)
                    results[owner] = self.runners[owner].evaluate(packet, self.action, deadline)
                    remaining[group] -= 1
                    if remaining[group] == 0:
                        group.finish_action(self.action, deadline)
                        pending.remove(group)
                except BaseException:
                    owners.add(owner)
                    raise
            if time.monotonic() >= deadline:
                owners.update(packets)
                raise RuntimeError("Late resident regulation action")
            return results
        except BaseException:
            # Global owner order can interleave several physical Stores. A group
            # left with shared inputs cannot be reused after an interrupted batch.
            for group in pending:
                owners.update(group.runners)
            self._fail(owners)
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
            for group in dict.fromkeys(self.groups.values()):
                group.reset(epoch, deadline)
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
