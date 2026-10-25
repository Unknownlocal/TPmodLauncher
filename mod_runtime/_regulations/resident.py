# SPDX-License-Identifier: MPL-2.0
"""Keep one mod's policy and action state inside the shared regulation group."""

from .action import RegulationAction
from .catalog import RegulationCatalog
from .policy import decision
from .transfer import RegulationTransfer
from .values import integer, loads


class RegulationResident:
    def __init__(self, handlers, *, cache):
        self.handlers = handlers
        self.cache = cache
        self.identity, self.active, self.staged = None, None, None
        self.transfer, self.action, self.contexts = None, None, None
        # One installed generation belongs to this career until reset. Before
        # commit there is no active view that could authorise an action.
        self.epoch, self.generation = 0, 0
        self.last_action, self.failed = 0, False
        self.staged_ready = False

    def fail(self):
        # Any failed operation is terminal, even if a caller ignores its error.
        # Clearing references also bounds retention after author exceptions.
        self.failed = True
        self.active = self.staged = self.transfer = self.action = None
        self.contexts = None

    def check(self):
        if self.failed:
            raise ValueError("Resident owner is unavailable")

    def ack(self, state):
        return {"epoch": self.epoch, "generation": self.generation, "state": state}

    def begin_policy(self, epoch, generation, length):
        self.check()
        integer(epoch, 64, positive=True)
        integer(generation, 64, positive=True)
        if self.active is not None or self.action is not None or self.transfer is not None or self.staged is not None:
            raise ValueError("Reset before installing another resident policy")
        if self.epoch not in (0, epoch) or generation <= self.generation:
            raise ValueError("Stale resident policy")
        self.bindings = self.handlers.seal()
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
        self.staged = RegulationCatalog(packet, self.bindings, cache=self.cache)
        # Bindings anchor the first owner's identity. Once admitted, that source
        # revision survives career resets for this Store.
        if self.staged.identity[0] != self.handlers.owner:
            raise ValueError("Resident policy belongs to another source owner")
        if self.identity is not None and self.staged.identity != self.identity:
            raise ValueError("Resident owner revision changed")
        self.identity = self.staged.identity
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
        self.revision = f"{self.identity[1]}:{epoch}:{generation}"
        return self.ack("active")

    def reset(self, epoch):
        self.check()
        integer(epoch, 64, positive=True)
        if epoch <= self.epoch:
            raise ValueError("Stale resident career reset")
        self.active = self.staged = self.transfer = self.action = None
        self.contexts = None
        self.epoch, self.generation = epoch, 0
        self.staged_ready = False
        # Keep action IDs increasing across careers so an old request cannot
        # match a new career's action. Bindings and owner identity also survive.
        return self.ack("reset")

    def begin_action(self, action, epoch, generation, length, first, *, contexts):
        self.check()
        integer(action, 64, positive=True)
        # Only the committed view of this career can authorise an action.
        if (self.action is not None or self.transfer is not None or self.staged is not None
                or self.active is None or action <= self.last_action
                or (epoch, generation) != (self.epoch, self.generation)):
            raise ValueError("Stale, unavailable or busy resident action")
        if integer(length, positive=True) > 384 * 1024:
            raise ValueError("Resident action packet exceeds its bound")
        self.last_action = action
        if contexts.action != action:
            raise ValueError("Foreign shared regulation context action")
        self.contexts = contexts
        self.transfer = RegulationTransfer("action", length)
        self.transfer.put(first)
        if self.transfer.remaining == 0:
            # Small packets finish in one call; chunked packets need seal_action
            # only after their final chunk. Both use the same validation path.
            return self.seal_action()
        return {"action": action, "prepared": 0}

    def seal_action(self):
        self.action = RegulationAction(loads(self.finish("action")), self.active, self.revision,
                                       contexts=self.contexts)
        self.contexts = None
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
