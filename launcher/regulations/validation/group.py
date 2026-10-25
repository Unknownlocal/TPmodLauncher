# SPDX-License-Identifier: MPL-2.0
"""Own one shared catalogue install, explicit mod calls and career reset."""

import time

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import decode, decision, fields
from mod_runtime._regulations.values import entry_fuel, install_fuel, integer
from mod_runtime.source_transfer import _chunks
from .install import PolicyInstall


class ValidationGroup:
    def __init__(self, handler):
        self.handler = handler
        self.active = self.staged = self.action = None
        self.epoch = self.generation = 0

    def _phase(self, fuel, seconds, deadline):
        remaining = min(seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise SandboxError("Resident regulation deadline expired")
        return self.handler.runner.sandbox.phase(fuel=fuel, seconds=remaining, deadline=deadline)

    def _ack(self, name, args, state, epoch, generation):
        result = self.handler.runner.sandbox.call(name, *args)
        if state is None:
            # Transfer exports are void and report failure by trapping. Policy
            # acknowledgements instead carry the exact identity/state below.
            if result is not None:
                raise SandboxError("Unexpected resident void acknowledgement")
            return
        value = decode(result)
        fields(value, ("epoch", "generation", "state"))
        integer(value["epoch"], 64, positive=True)
        integer(value["generation"], 64)
        if value != {"epoch": epoch, "generation": generation, "state": state}:
            raise SandboxError("Foreign resident policy acknowledgement")

    def stage(self, views, epoch, generation, deadline):
        """Transfer one complete manifest and one copy of each physical record."""
        try:
            started = time.monotonic()
            if self.active is not None or self.staged is not None or views.keys() != self.handler.validators.keys():
                raise ValueError("Reset before installing a complete shared policy")
            if any(view.owner != owner or view.validators != self.handler.validators[owner]
                   for owner, view in views.items()):
                raise ValueError("Resident view differs from admitted handler bindings")
            plan = PolicyInstall(views, epoch, generation)
            with self._phase(plan.fuel - plan.commit_fuel, 5 * plan.owners - (time.monotonic() - started), deadline):
                for name, args, state in plan.operations:
                    self._ack(name, args, state, epoch, generation)
            # The resident plan only needs reset metrics and record references.
            # Release sent JSON here, charging that cleanup to install time.
            plan.operations.clear()
            self._control_left = 5 * plan.owners - (time.monotonic() - started)
            self.staged, self.epoch, self.generation = plan, epoch, generation
        except BaseException:
            self.handler.runner.close()
            raise

    def commit(self, epoch, generation, deadline):
        """A matching acknowledgement activates every owner atomically."""
        try:
            if self.staged is None or (epoch, generation) != (self.epoch, self.generation):
                raise ValueError("No matching staged resident publication")
            with self._phase(self.staged.commit_fuel, self._control_left, deadline):
                self._ack("commit-regulation-policy", (epoch, generation), "active", epoch, generation)
            self.active, self.staged = self.staged, None
        except BaseException:
            self.handler.runner.close()
            raise

    def begin_action(self, packet, action, deadline):
        try:
            if self.action is not None or self.staged is not None:
                raise ValueError("Shared regulation group is busy")
            text, metric, count = packet
            chunks = list(_chunks(text.encode("utf-8")))
            fuel = entry_fuel(metric[0], metric[1], len(chunks) + 2, metric[3])
            sandbox = self.handler.runner.sandbox
            with self._phase(fuel, .1, deadline):
                reply = sandbox.call("begin-regulation-contexts", action, metric[0], chunks[0])
                if len(chunks) > 1:
                    self._contexts(reply, action, 0)
                    for chunk in chunks[1:]:
                        self._ack("put-regulation-contexts", (chunk,), None, 0, 0)
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
            with self._phase(fuel, .1, deadline):
                self._ack("finish-regulation-action", (action,), None, 0, 0)
            self.action = None
            if time.monotonic() >= deadline:
                raise SandboxError("Late shared regulation action")
        except BaseException:
            self.handler.runner.close()
            raise

    def evaluate(self, owner, packet, action, deadline):
        try:
            if self.active is None or self.staged is not None:
                raise ValueError("Resident owner has no current published view")
            text, metric, remaining = packet
            chunks = list(_chunks(text.encode("utf-8")))
            fuel = entry_fuel(metric[0], metric[1], len(chunks) + 2, metric[3])
            results = []
            with self._phase(fuel, .1, deadline):
                reply = self.handler.runner.sandbox.call("begin-regulation-action", owner, action, self.epoch, self.generation,
                                                         metric[0], chunks[0])
                if len(chunks) > 1:
                    self._prepared(reply, action, 0)
                    for chunk in chunks[1:]:
                        self.handler.runner.sandbox.call("put-regulation-action", chunk)
                    reply = self.handler.runner.sandbox.call("seal-regulation-action")
                while remaining:
                    count = min(4, remaining)
                    self._prepared(reply, action, count)
                    for slot in range(count):
                        value = decode(self.handler.runner.sandbox.call("validate-resident-regulation", action, slot, fuel=5_000_000))
                        fields(value, ("action", "slot", "result"))
                        if integer(value["action"], 64, positive=True) != action or integer(value["slot"]) != slot:
                            raise SandboxError("Foreign resident action reply")
                        results.append(decision(value["result"]))
                    remaining -= count
                    if remaining:
                        reply = self.handler.runner.sandbox.call("prepare-regulation-slots", action, fuel=64_000_000)
            if time.monotonic() >= deadline:
                raise SandboxError("Late resident regulation action")
            return results
        except BaseException:
            self.handler.runner.close()
            raise

    def _prepared(self, reply, action, count):
        value = decode(reply)
        fields(value, ("action", "prepared"))
        # The expected group size is derived from our remaining calls, never
        # accepted as a guest-selected count or a reason to skip validators.
        if integer(value["action"], 64, positive=True) != action or integer(value["prepared"]) != count:
            raise SandboxError("Foreign resident preparation acknowledgement")

    def reset(self, epoch, deadline):
        try:
            plan = self.active or self.staged
            nodes = plan.metric[1] + sum(record.metric[1] for record in plan.records) if plan is not None else 0
            count = len(plan.records) if plan is not None else 0
            with self._phase(install_fuel(0, nodes, count, 1, 0), 5 * len(self.handler.validators), deadline):
                self._ack("reset-regulation-group", (epoch,), "reset", epoch, 0)
            self.active = self.staged = self.action = None
            self.epoch, self.generation = epoch, 0
        except BaseException:
            self.handler.runner.close()
            raise
