# SPDX-License-Identifier: MPL-2.0
"""Coordinate shared storage while retaining a separate execution view per owner."""

import time

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import decode, fields
from mod_runtime._regulations.values import entry_fuel, install_fuel, integer
from mod_runtime.source_transfer import _chunks
from .runner import ValidationRunner


class ValidationGroup:
    """Own one physical runner's publication cache, shared inputs and reset.

    Member ValidationRunners retain their own generations and binding checks.
    This coordinator never changes author host-call identity or enters in parallel.
    """

    def __init__(self, handler):
        self.handler = handler
        self.runners = {owner: ValidationRunner(handler, owner=owner) for owner in handler.validators}
        self.publication = self.action = None

    @property
    def helper(self):
        """Reuse the existing checked phase/acknowledgement code for group exports."""
        return next(iter(self.runners.values()))

    def begin_publication(self, owners):
        started = time.monotonic()
        if self.publication is not None:
            raise ValueError("Shared publication is already pending")
        records = {record.identity: record for runner in self.runners.values() if runner.active is not None
                   for record in runner.active.records}
        self.publication = owners, records
        # Common host bookkeeping spends the group's remaining control time;
        # it does not receive a fresh allowance after the owner commits.
        self._overhead = time.monotonic() - started

    def stage(self, owner, view, epoch, generation, deadline):
        _, records = self.publication
        self.runners[owner].stage(view, epoch, generation, deadline, known=records.keys())
        started = time.monotonic()
        records.update((record.identity, record) for record in view.records)
        self._overhead += time.monotonic() - started

    def finish_publication(self, deadline):
        try:
            started = time.monotonic()
            owners, previous = self.publication
            active = [runner.active for runner in self.runners.values() if runner.active is not None]
            reachable = {record.identity for view in active for record in view.records}
            # Old payloads remain in the group's cache after individual commits.
            # Count their final release here, together with every index scanned.
            nodes = sum(view.manifest()[1][1] for view in active)
            nodes += sum(record.metric[1] for identity, record in previous.items() if identity not in reachable)
            fuel = install_fuel(0, nodes, len(previous), 1, 0)
            seconds = sum(self.runners[owner]._control_left for owner in owners)
            seconds -= self._overhead + time.monotonic() - started
            with self.helper._phase(fuel, seconds, deadline):
                self.helper._ack("finish-regulation-publication", (), None, 0, 0)
            self.publication = None
        except BaseException:
            self.handler.runner.close()
            raise

    def begin_action(self, packet, action, deadline):
        try:
            if self.action is not None or self.publication is not None:
                raise ValueError("Shared regulation group is busy")
            text, metric, count = packet
            chunks = list(_chunks(text.encode("utf-8")))
            fuel = entry_fuel(metric[0], metric[1], len(chunks) + 2, metric[3])
            sandbox = self.handler.runner.sandbox
            with self.helper._phase(fuel, .1, deadline):
                reply = sandbox.call("begin-regulation-contexts", action, metric[0], chunks[0])
                if len(chunks) > 1:
                    self._contexts(reply, action, 0)
                    for chunk in chunks[1:]:
                        self.helper._ack("put-regulation-contexts", (chunk,), None, 0, 0)
                    reply = sandbox.call("seal-regulation-contexts")
                self._contexts(reply, action, count)
            self.action = action, metric
        except BaseException:
            self.handler.runner.close()
            raise

    def _contexts(self, reply, action, count):
        value = decode(reply)
        fields(value, ("action", "contexts"))
        if integer(value["action"], 64, positive=True) != action or integer(value["contexts"]) != count:
            raise SandboxError("Foreign shared context acknowledgement")

    def finish_action(self, action, deadline):
        try:
            if self.action is None or self.action[0] != action:
                raise ValueError("Unknown shared regulation action")
            # Clearing the canonical context table is framework work too; it
            # cannot consume the final validator's ordinary fuel allowance.
            fuel = entry_fuel(0, self.action[1][1], 1, 0)
            with self.helper._phase(fuel, .1, deadline):
                self.helper._ack("finish-regulation-action", (action,), None, 0, 0)
            self.action = None
            if time.monotonic() >= deadline:
                raise SandboxError("Late shared regulation action")
        except BaseException:
            self.handler.runner.close()
            raise

    def reset(self, epoch, deadline):
        try:
            views = [view for runner in self.runners.values() for view in (runner.active, runner.staged) if view is not None]
            records = {record.identity: record for view in views for record in view.records}
            nodes = sum(view.manifest()[1][1] for view in views) + sum(record.metric[1] for record in records.values())
            with self.helper._phase(install_fuel(0, nodes, len(records), 1, 0), 5 * len(self.runners), deadline):
                self.helper._ack("reset-regulation-group", (epoch,), "reset", epoch, 0)
            for runner in self.runners.values():
                runner.active = runner.staged = None
                runner.epoch, runner.generation, runner.required = epoch, 0, 0
            self.publication = self.action = None
        except BaseException:
            self.handler.runner.close()
            raise
