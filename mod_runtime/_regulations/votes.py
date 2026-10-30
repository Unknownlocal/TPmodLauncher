# SPDX-License-Identifier: MPL-2.0
"""Retain one proposal's shared snapshot while preparing independent team calls."""

import gc

from .frozen import FrozenValue
from .values import integer
from .voting import voting_packet


class RegulationVotes:
    """Copy arguments before author fuel starts, then consume each call once."""

    def __init__(self, packet, handlers):
        voting_packet(packet)
        owner = packet["owner"]
        if owner not in handlers:
            raise ValueError("Unbound voting owner")
        bindings = handlers[owner].seal()
        index = packet["binding"]
        if index >= len(bindings) or bindings[index][2] != packet["regulation"]["key"]:
            raise ValueError("Foreign voting binding")
        self.function = bindings[index][0]
        # This snapshot lasts one batch. Container templates avoid interpreting
        # every scalar again for each team; persistent rules keep flat storage.
        self.snapshot = FrozenValue(packet["snapshot"], templates=True)
        self.rule = FrozenValue(packet["regulation"])
        self.contexts = [FrozenValue(context) for context in packet["contexts"]]
        self.next, self.prepared = 0, None
        gc.collect(0)

    def prepare(self, slot):
        if integer(slot) != self.next or self.prepared is not None or self.next >= len(self.contexts):
            raise ValueError("Repeated or out-of-order voting preparation")
        context = self.contexts[slot].copy()
        context["snapshot"] = self.snapshot.copy()
        self.prepared = context, self.rule.copy()
        self.contexts[slot] = None

    def take(self, slot):
        if integer(slot) != self.next or self.prepared is None:
            raise ValueError("Unprepared or repeated voting calculation")
        arguments, self.prepared = self.prepared, None
        self.next += 1
        return arguments

    def finish(self):
        if self.prepared is not None or self.next != len(self.contexts):
            raise ValueError("Voting calculations are incomplete")
