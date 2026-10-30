# SPDX-License-Identifier: MPL-2.0
"""Execute one proposal's team calculations with a single shared snapshot transfer."""

import math
import time

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import decode, encode, fields
from mod_runtime._regulations.values import entry_fuel, integer
from mod_runtime._regulations.voting import vote_reply, voting_packet
from mod_runtime.source_transfer import _chunks
from .shared_handler_runner import SharedHandlerRunner


class VotingRunner:
    """Return results only after every addressed call and batch cleanup succeeds."""

    def __init__(self, handler):
        if type(handler) is not SharedHandlerRunner or not handler.voting:
            raise ValueError("Expected bound shared voting handlers")
        self.handler = handler

    def evaluate(self, owner, snapshot, regulation, contexts, *, deadline):
        """Prepare arguments separately from each handler's execution allowance."""
        runner = self.handler.runner
        try:
            if type(deadline) not in (int, float) or not math.isfinite(deadline):
                raise ValueError("Expected a finite voting deadline")
            deadline = min(deadline, time.monotonic() + 5)
            binding = self.handler.voting[owner][regulation["key"]][0]
            packet = {"owner": owner, "binding": binding, "snapshot": snapshot,
                      "regulation": regulation, "contexts": contexts}
            metric = voting_packet(packet)
            encoded = encode(packet)
            chunks = list(_chunks(encoded.encode("utf-8")))
            self.handler._vote_action = action = integer(self.handler._vote_action + 1, 64, positive=True)
            fuel = entry_fuel(metric[0], metric[1], len(contexts), metric[3])
            with runner.sandbox.phase(fuel=fuel, seconds=5, deadline=deadline):
                reply = runner.sandbox.call("begin-regulation-vote", action, metric[0], chunks[0])
                if len(chunks) > 1:
                    self._ack(reply, action, "calls", 0)
                    for chunk in chunks[1:]:
                        runner.sandbox.call("put-regulation-vote", chunk)
                    reply = runner.sandbox.call("seal-regulation-vote")
                self._ack(reply, action, "calls", len(contexts))
            results = {}
            for slot, context in enumerate(contexts):
                with runner.sandbox.phase(fuel=fuel, seconds=5, deadline=deadline):
                    reply = runner.sandbox.call("prepare-regulation-vote", action, slot)
                    self._ack(reply, action, "slot", slot)
                # Copying a large frozen snapshot must not enlarge the author's
                # ordinary fuel, time or host-call allowance.
                with runner.sandbox.phase(fuel=5_000_000, seconds=1, deadline=deadline):
                    reply = runner.sandbox.call("evaluate-regulation-vote", action, slot)
                    results[context["team"]] = vote_reply(reply, action, slot)
            with runner.sandbox.phase(fuel=fuel, seconds=5, deadline=deadline):
                if runner.sandbox.call("finish-regulation-vote", action) is not None:
                    raise SandboxError("Unexpected voting completion acknowledgement")
            if time.monotonic() >= deadline:
                raise SandboxError("Late voting calculation batch")
            return results
        except BaseException:
            runner.close()
            raise

    def _ack(self, reply, action, field, expected):
        value = decode(reply)
        fields(value, ("action", field))
        if integer(value["action"], 64, positive=True) != action or integer(value[field]) != expected:
            raise SandboxError("Foreign voting acknowledgement")
