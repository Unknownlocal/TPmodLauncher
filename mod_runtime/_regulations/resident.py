# SPDX-License-Identifier: MPL-2.0
"""Career/policy/action lifecycle for one sandbox's private regulation adapter."""

from .action import RegulationAction
from .catalog import RegulationCatalog
from .policy import decision
from .transfer import RegulationTransfer
from .values import integer, loads


class RegulationResident:
    def __init__(self, handlers, *, cache=None):
        self.handlers = handlers
        self.cache = cache
        self.identity, self.active, self.staged = None, None, None
        self.transfer, self.action, self.contexts = None, None, None
        # epoch identifies the career; generation is active, required is the
        # newest requested publication (which may still be incomplete).
        self.epoch, self.generation, self.required = 0, 0, 0
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
        return {"epoch": self.epoch, "generation": self.required, "state": state}

    def begin_policy(self, epoch, generation, length):
        self.check()
        integer(epoch, 64, positive=True)
        integer(generation, 64, positive=True)
        # One operation owns the mutable staging area. Updating during an action
        # would invalidate the references that action has already resolved.
        if self.action is not None or self.transfer is not None or self.staged is not None:
            raise ValueError("Resident state is busy")
        # Initial setup may establish a career; later career changes require reset.
        if self.epoch not in (0, epoch) or generation <= self.required:
            raise ValueError("Stale resident policy")
        self.bindings = self.handlers.seal()
        self.epoch, self.required = epoch, generation
        # required advances before receiving data: old active generations cannot
        # authorize an action while the replacement is partial or uncommitted.
        integer(length)
        self.staged_ready = False
        if length == 0:
            if self.active is None:
                raise ValueError("No active view for a metadata-only publication")
            # Membership/payloads are unchanged. Stage their existing immutable
            # view instead of retransferring a potentially large manifest.
            self.staged = self.active
            return self.ack("manifest")
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
        self.staged = RegulationCatalog(packet, self.bindings, self.active, cache=self.cache)
        # Bindings anchor the first owner's identity. Once admitted, that source
        # revision survives policy updates and career resets for this Store.
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
        # Metadata-only publication borrows the already sealed active catalog;
        # its new publication still needs this explicit seal before commit.
        if policy is not self.active:
            policy.seal()
        self.staged_ready = True
        return self.ack("sealed")

    def commit_policy(self, epoch, generation):
        policy = self.policy()
        # Completion alone is insufficient: commit must identify the exact staged
        # publication, so an acknowledgement from an older update cannot activate it.
        if (epoch, generation) != (self.epoch, self.required) or not self.staged_ready:
            raise ValueError("Unsealed or foreign resident publication")
        self.active, self.staged, self.generation = policy, None, generation
        self.revision = f"{self.identity[1]}:{epoch}:{generation}"
        return self.ack("active")

    def reset(self, epoch):
        self.check()
        integer(epoch, 64, positive=True)
        if epoch <= self.epoch:
            raise ValueError("Stale resident career reset")
        self.active = self.staged = self.transfer = self.action = None
        self.contexts = None
        self.epoch, self.generation, self.required = epoch, 0, 0
        self.staged_ready = False
        # Action IDs remain monotonic across epochs: an old discard must never
        # match and discard a new career's action. Bindings/owner identity survive.
        return self.ack("reset")

    def begin_action(self, action, epoch, generation, length, first, *, contexts=None):
        self.check()
        integer(action, 64, positive=True)
        # Both the active and required generations must match: an old active view
        # cannot authorize work while its replacement is staged or incomplete.
        if (self.action is not None or self.transfer is not None or self.staged is not None
                or self.active is None or action <= self.last_action
                or (epoch, generation) != (self.epoch, self.generation) or generation != self.required):
            raise ValueError("Stale, unavailable or busy resident action")
        if integer(length, positive=True) > 384 * 1024:
            raise ValueError("Resident action packet exceeds its bound")
        self.last_action = action
        if contexts is not None and contexts.action != action:
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

    def discard(self, action):
        self.check()
        # Discarding the latest completed action is harmless; an older ID must
        # never discard a newer action. Policy transfers have a separate lifetime.
        if action != self.last_action or not action:
            raise ValueError("Stale resident discard")
        if self.transfer is not None and self.transfer.kind != "action":
            raise ValueError("Cannot discard a policy transfer as an action")
        self.action = self.transfer = None
        self.contexts = None
