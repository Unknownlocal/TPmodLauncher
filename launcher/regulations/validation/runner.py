# SPDX-License-Identifier: MPL-2.0
"""Checked resident exports for one owner's view within a shared runner."""

import time

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import decode, decision, fields
from mod_runtime._regulations.values import entry_fuel, integer
from mod_runtime.source_transfer import _chunks
from .install import PolicyInstall


class ValidationRunner:
    def __init__(self, handler, *, owner):
        self.handler, self.active, self.staged = handler, None, None
        self.owner = owner
        self.epoch, self.generation, self.required = 0, 0, 0

    def _phase(self, fuel, seconds, deadline):
        remaining = min(seconds, deadline - time.monotonic())
        if remaining <= 0:
            raise SandboxError("Resident regulation deadline expired")
        return self.handler.runner.sandbox.phase(fuel=fuel, seconds=remaining, deadline=deadline)

    def _select(self):
        # Selection addresses private resident state. Author host calls
        # retain the identity bound into each imported source module.
        self.handler.runner.sandbox.call("select-regulation-owner", self.owner)

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

    def stage(self, view, epoch, generation, deadline, *, known=None):
        try:
            started = time.monotonic()
            self.required = generation
            # The host fences the old generation before any transfer starts,
            # including failures during plan construction or binding comparison.
            validators = self.handler.validators[self.owner]
            if (self.staged is not None or view.owner != self.owner or view.validators != validators):
                raise ValueError("Resident view differs from admitted handler bindings")
            plan = PolicyInstall(view, self.active, epoch, generation, known=known)
            # Shared phases first select the owner's stored regulation state.
            # Count that extra export in both staging and commit allowances.
            crossing = 250_000
            self._commit_fuel = plan.commit_fuel + crossing
            with self._phase(plan.fuel - plan.commit_fuel + crossing, 5 - (time.monotonic() - started), deadline):
                self._select()
                for name, args, state in plan.operations:
                    self._ack(name, args, state, epoch, generation)
            self._control_left = 5 - (time.monotonic() - started)
            self.staged, self.epoch = view, epoch
        except BaseException:
            self.handler.runner.close()
            raise

    def commit(self, epoch, generation, deadline):
        try:
            started = time.monotonic()
            if self.staged is None or (epoch, generation) != (self.epoch, self.required):
                raise ValueError("No matching staged resident publication")
            # Other owners may take time to stage. Carry this Store's unspent
            # allowance into commit; the original transaction deadline still runs
            # while waiting, and neither phase gets another five seconds.
            with self._phase(self._commit_fuel, self._control_left, deadline):
                self._select()
                self._ack("commit-regulation-policy", (epoch, generation), "active", epoch, generation)
            self.active, self.staged, self.generation = self.staged, None, generation
            self._control_left -= time.monotonic() - started
        except BaseException:
            self.handler.runner.close()
            raise

    def evaluate(self, packet, action, deadline):
        try:
            if self.active is None or self.staged is not None or self.required != self.generation:
                raise ValueError("Resident owner has no current published view")
            text, metric, remaining = packet
            chunks = list(_chunks(text.encode("utf-8")))
            fuel = entry_fuel(metric[0], metric[1], len(chunks) + 3, metric[3])
            results = []
            with self._phase(fuel, .1, deadline):
                self._select()
                reply = self.handler.runner.sandbox.call("begin-regulation-action", action, self.epoch, self.generation,
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
