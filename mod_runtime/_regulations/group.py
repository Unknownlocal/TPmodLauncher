# SPDX-License-Identifier: MPL-2.0
"""Share rule storage and proposal inputs without merging mod identities."""

from .contexts import RegulationContexts
from .handlers import RegulationHandlers
from .policy import decode
from .resident import RegulationResident
from .transfer import RegulationTransfer
from .values import integer, loads


class RegulationGroup:
    """Own private per-mod residents, one record cache and one action context.

    Owner selection is a cursor for serial trusted export calls, not the identity
    of author host calls. Each author's wit_world module keeps its bound ID.
    """

    def __init__(self, sources):
        self.sources = sources
        self.residents, self.records = {}, {}
        self.selected = self.transfer = self.contexts = None
        self.last_action = 0
        self.failed = self.sealed = False

    def check(self):
        if self.failed or self.sources.failed:
            raise ValueError("Shared regulation group is unavailable")

    def fail(self):
        """Make every resident unavailable; partial successful rules cannot remain."""
        self.failed = True
        self.transfer = self.contexts = None
        for resident in self.residents.values():
            resident.fail()
        self.records.clear()

    def bind(self, index, declaration):
        self.check()
        if self.sealed:
            raise ValueError("Shared regulation bindings are sealed")
        owner = decode(declaration)["owner"]
        loader = self.sources._owner(owner)
        if not loader.activated:
            raise ValueError("Activate before binding shared regulation handlers")
        if owner not in self.residents:
            handlers = RegulationHandlers(loader.sources, owner=owner, namespace=loader.namespace)
            self.residents[owner] = RegulationResident(handlers, cache=self.records)
        # Every table keeps its existing 32-binding limit. The group is bounded
        # separately by the admitted source-owner limit, rather than 32 total.
        self.residents[owner].handlers.bind(index, declaration)

    def select(self, owner):
        self.check()
        if owner not in self.residents:
            raise ValueError("Unknown shared regulation owner")
        self.sealed = True
        self.selected = owner

    def resident(self):
        self.check()
        if self.selected is None:
            raise ValueError("Select a shared regulation owner before resident calls")
        return self.residents[self.selected]

    def call(self, method, *arguments):
        """Apply the selected owner's operation without interleaving lifetimes."""
        resident = self.resident()
        if method == "begin_action":
            return self.begin_action(*arguments)
        if method == "begin_policy" and (self.contexts is not None or self.transfer is not None):
            raise ValueError("Cannot publish during a shared regulation action")
        return getattr(resident, method)(*arguments)

    def begin_contexts(self, action, length, first):
        self.check()
        integer(action, 64, positive=True)
        if not self.sealed or action <= self.last_action or self.transfer is not None or self.contexts is not None:
            raise ValueError("Stale or busy shared regulation action")
        for resident in self.residents.values():
            if (resident.active is None or resident.staged is not None or resident.transfer is not None
                    or resident.action is not None or resident.generation != resident.required):
                raise ValueError("Shared regulation policy is not current")
        # The global action table admits 1024 contexts. Each still has the usual
        # 4 KiB invocation bound; chunks are only 4 KiB, not the complete table.
        if integer(length, positive=True) > 4 * 1024 * 1024:
            raise ValueError("Shared regulation contexts exceed their transfer bound")
        self.last_action = action
        self.transfer = RegulationTransfer("contexts", length)
        self.transfer.put(first)
        if self.transfer.remaining == 0:
            return self.seal_contexts()
        return {"action": action, "contexts": 0}

    def put_contexts(self, chunk):
        self.check()
        if self.transfer is None:
            raise ValueError("Missing shared regulation context transfer")
        self.transfer.put(chunk)

    def seal_contexts(self):
        self.check()
        if self.transfer is None:
            raise ValueError("Missing shared regulation context transfer")
        values = loads(self.transfer.finish("contexts"))
        self.transfer = None
        self.contexts = RegulationContexts(self.last_action, values)
        return {"action": self.last_action, "contexts": len(self.contexts.rows)}

    def begin_action(self, action, *arguments):
        if self.contexts is None or self.contexts.action != action:
            raise ValueError("Missing or foreign shared regulation contexts")
        return self.resident().begin_action(action, *arguments, contexts=self.contexts)

    def finish_action(self, action):
        """Complete the whole action before the host may accept its decisions."""
        self.check()
        if self.contexts is None or self.contexts.action != action:
            raise ValueError("Missing or foreign shared regulation action")
        if any(resident.action is not None or resident.transfer is not None for resident in self.residents.values()):
            raise ValueError("Shared regulation calls are incomplete")
        self.contexts.seal()
        self.contexts = None

    def finish_publication(self):
        """Retire old cached records once all owner commits have completed."""
        self.check()
        if self.contexts is not None or self.transfer is not None:
            raise ValueError("Shared regulation action is busy")
        reachable = set()
        for resident in self.residents.values():
            if (resident.staged is not None or resident.transfer is not None or resident.action is not None
                    or resident.generation != resident.required):
                raise ValueError("Shared regulation publication is incomplete")
            if resident.active is not None:
                reachable.update(resident.active.by_id)
        for identity in tuple(self.records):
            if identity not in reachable:
                del self.records[identity]

    def reset(self, epoch):
        self.check()
        if not self.sealed:
            raise ValueError("Shared regulation bindings are not sealed")
        for resident in self.residents.values():
            resident.reset(epoch)
        self.contexts = self.transfer = None
        self.records.clear()
        return {"epoch": epoch, "generation": 0, "state": "reset"}
