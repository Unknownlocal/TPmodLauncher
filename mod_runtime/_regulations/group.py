# SPDX-License-Identifier: MPL-2.0
"""Own one shared career install and action lifetime, retaining original mod IDs."""

from .action import RegulationAction
from .catalog import RegulationCatalog
from .contexts import RegulationContexts
from .handlers import RegulationHandlers
from .policy import decode, decision
from .transfer import RegulationTransfer
from .values import integer, loads, text


class RegulationGroup:
    """Store physical records once and validate explicitly addressed mod calls.

    Each author module keeps its bound host-call identity. An owner argument
    selects its checked rule view; no mutable owner-selection cursor is needed.
    """

    def __init__(self, sources):
        self.sources, self.handlers = sources, {}
        self.identities = self.active = self.staged = None
        self.transfer = self.contexts = self.action = None
        self.epoch = self.generation = self.last_action = 0
        self.failed = self.sealed = self.staged_ready = False
        self._action_owners = set()

    def check(self):
        if self.failed or self.sources.failed:
            raise ValueError("Shared regulation group is unavailable")

    def fail(self):
        self.failed = True
        self.active = self.staged = self.transfer = self.contexts = self.action = None
        self._action_owners.clear()

    def bind(self, index, declaration):
        self.check()
        if self.sealed:
            raise ValueError("Shared regulation bindings are sealed")
        owner = decode(declaration)["owner"]
        loader = self.sources._owner(owner)
        if not loader.activated:
            raise ValueError("Activate before binding shared regulation handlers")
        if owner not in self.handlers:
            self.handlers[owner] = RegulationHandlers(loader.sources, owner=owner, namespace=loader.namespace)
        self.handlers[owner].bind(index, declaration)

    def ack(self, state):
        return {"epoch": self.epoch, "generation": self.generation, "state": state}

    def begin_policy(self, epoch, generation, length):
        self.check()
        integer(epoch, 64, positive=True)
        integer(generation, 64, positive=True)
        # Definition-only packs need no guest catalogue. Execution policy must
        # be anchored to at least one admitted source handler.
        if not self.handlers:
            raise ValueError("Bind regulation owners before policy transfer")
        if self.active is not None or self.action is not None or self.transfer is not None or self.staged is not None:
            raise ValueError("Reset before installing another resident policy")
        if self.epoch not in (0, epoch) or generation <= self.generation:
            raise ValueError("Stale resident policy")
        self.sealed = True
        self.bindings = {owner: handlers.seal() for owner, handlers in self.handlers.items()}
        integer(length, positive=True)
        self.epoch, self.generation = epoch, generation
        self.staged_ready = False
        self.transfer = RegulationTransfer("manifest", length)
        return self.ack("receiving")

    def put(self, kind, chunk):
        self.check()
        if self.transfer is None or self.transfer.kind != kind:
            raise ValueError("Wrong resident transfer")
        self.transfer.put(chunk)

    def finish(self, kind):
        self.check()
        if self.transfer is None:
            raise ValueError("Missing resident transfer")
        value = self.transfer.finish(kind)
        self.transfer = None
        return value

    def seal_manifest(self):
        packet = loads(self.finish("manifest"))
        self.staged = RegulationCatalog(packet, self.bindings)
        identities = {owner: view.identity for owner, view in self.staged.views.items()}
        if self.identities is not None and identities != self.identities:
            raise ValueError("Resident owner revisions changed")
        self.identities = identities
        return self.ack("manifest")

    def policy(self):
        self.check()
        # No pages, seals or commits may interleave with an incomplete transfer.
        if self.staged is None or self.transfer is not None:
            raise ValueError("Missing or busy resident policy")
        return self.staged

    def install_records(self, page):
        self.policy().install_page(page)
        return self.ack("records")

    def begin_record(self, slot, length):
        policy = self.policy()
        slot = integer(slot)
        # Individual transfer is for a declared, missing record. Its exact byte
        # length must match the descriptor before chunks are accepted.
        if slot >= len(policy.descriptors) or slot in policy.records or length != policy.descriptors[slot][2][0]:
            raise ValueError("Foreign or repeated resident record transfer")
        self.transfer = RegulationTransfer(slot, length)

    def put_record(self, chunk):
        self.check()
        # Integer transfer kinds identify record slots; strings identify manifest
        # or action transfers. Never feed a record chunk into the other paths.
        if self.transfer is None or type(self.transfer.kind) is not int:
            raise ValueError("Missing resident record transfer")
        self.transfer.put(chunk)

    def seal_record(self):
        self.check()
        if self.transfer is None or type(self.transfer.kind) is not int:
            raise ValueError("Missing resident record transfer")
        slot = self.transfer.kind
        self.staged.install(slot, loads(self.finish(slot)))
        return self.ack("records")

    def seal_policy(self):
        policy = self.policy()
        if self.staged_ready:
            raise ValueError("Repeated resident policy seal")
        policy.seal()
        self.staged_ready = True
        return self.ack("sealed")

    def commit_policy(self, epoch, generation):
        policy = self.policy()
        # Completion alone is insufficient: commit must identify the exact staged
        # publication; another career's acknowledgement cannot activate it.
        if (epoch, generation) != (self.epoch, self.generation) or not self.staged_ready:
            raise ValueError("Unsealed or foreign resident publication")
        self.active, self.staged = policy, None
        return self.ack("active")

    def begin_contexts(self, action, proposal):
        self.check()
        integer(action, 64, positive=True)
        if not self.sealed or action <= self.last_action or self.transfer is not None or self.contexts is not None:
            raise ValueError("Stale or busy shared regulation action")
        if self.active is None or self.staged is not None or self.action is not None:
            raise ValueError("Shared regulation policy is not current")
        # A complete context+rule argument fits 4 KiB. Removing its nonempty
        # rule leaves enough room for [context], so this transfer needs no pages.
        if len(text(proposal, 4096).encode("utf-8")) > 4096:
            raise ValueError("Shared regulation context exceeds its transfer bound")
        self.contexts = RegulationContexts(action, loads(proposal))
        self.last_action = action
        self._action_owners.clear()
        return {"action": action, "contexts": 1}

    def begin_action(self, owner, action, epoch, generation, length, first):
        self.check()
        integer(action, 64, positive=True)
        if (self.action is not None or self.transfer is not None or self.staged is not None
                or self.active is None or (epoch, generation) != (self.epoch, self.generation)
                or self.contexts is None or self.contexts.action != action):
            raise ValueError("Stale, unavailable or busy resident action")
        if owner not in self.active.views or owner in self._action_owners:
            raise ValueError("Foreign or repeated regulation action owner")
        self._action_owners.add(owner)
        self._action_owner = owner
        if integer(length, positive=True) > 384 * 1024:
            raise ValueError("Resident action packet exceeds its bound")
        self.transfer = RegulationTransfer("action", length)
        self.transfer.put(first)
        if self.transfer.remaining == 0:
            # Small packets finish in one call; chunked packets need seal_action
            # only after their final chunk. Both use the same validation path.
            return self.seal_action()
        return {"action": action, "prepared": 0}

    def seal_action(self):
        view = self.active.views[self._action_owner]
        revision = f"{view.identity[1]}:{self.epoch}:{self.generation}"
        self.action = RegulationAction(loads(self.finish("action")), view, revision, contexts=self.contexts)
        return {"action": self.last_action, "prepared": len(self.action.prepared)}

    def prepare(self, action):
        self.check_action(action)
        return {"action": action, "prepared": self.action.prepare()}

    def check_action(self, action):
        self.check()
        if action != self.last_action or self.action is None:
            raise ValueError("Unknown resident action")

    def validate(self, action, slot):
        self.check_action(action)
        function, context, rule = self.action.take(slot)
        if self.action.finished:
            self.action = None  # Release action-owned canonicals before author code.
        return {"action": action, "slot": slot, "result": decision(function(context, rule))}

    def finish_action(self, action):
        """Accept results only after all calls and shared inputs are consumed."""
        self.check()
        if self.contexts is None or self.contexts.action != action:
            raise ValueError("Missing or foreign shared regulation action")
        if self.action is not None or self.transfer is not None:
            raise ValueError("Shared regulation calls are incomplete")
        self.contexts.seal()
        self.contexts = None

    def reset(self, epoch):
        self.check()
        integer(epoch, 64, positive=True)
        if not self.sealed or epoch <= self.epoch:
            raise ValueError("Stale or unbound regulation career reset")
        self.active = self.staged = self.transfer = self.contexts = self.action = None
        self.epoch, self.generation, self.staged_ready = epoch, 0, False
        self._action_owners.clear()
        # Bindings/revisions and the last action ID survive to reject old work.
        return self.ack("reset")
