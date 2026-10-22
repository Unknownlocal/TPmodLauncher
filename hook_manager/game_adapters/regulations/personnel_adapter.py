# SPDX-License-Identifier: MPL-2.0
"""Guard Formula driver/staff signing with one shared negotiation owner."""

from functools import update_wrapper

from ...callables import CallBinding, ResultContract
from ...manager import HookError, Manager
from .guards import RegulationGuards
from .contexts import regulation_context
from ..personnel.caller_targets import personnel_caller_targets, personnel_mail_target
from ..personnel.callers import PersonnelCallers
from ..personnel.feedback import PersonnelFeedback
from ..drivers.planner import DriverContractPlanner
from ..personnel.preparation import publish_preparation
from ..personnel.preparation_methods import check_preparation
from ..drivers.targets import driver_target
from ..staff.planner import StaffContractPlanner
from ..staff.targets import staff_target


class PersonnelAdapter(RegulationGuards):
    @staticmethod
    def _caller_targets(state, kinds):
        targets = personnel_caller_targets(kinds)
        # An owned/headless state can omit mail; a real mailbox must participate
        # in the same preflight/install group as the negotiation callers.
        return (*targets, personnel_mail_target()) if getattr(state, "email_manager", None) is not None else targets

    @classmethod
    def create(cls, state, channel, select, report):
        def failure(kind, error):
            report("REGULATIONS COMPATIBILITY FAILED scope=personnel.contract " + kind + ": "
                   + " ".join(str(error).split())[:512] + "; " + kind
                   + " integration skipped; continuing native game")

        try:
            # Disabled/unsubscribed mods must not trigger inspection or hooks.
            if not channel.bindings("personnel.contract"):
                return None
            planners = {}
            # Select compatible kinds before installing anything. A changed
            # driver-only helper must not prevent usable staff guards (or vice versa).
            for kind, factory, target in (("driver", DriverContractPlanner, driver_target),
                                          ("staff", StaffContractPlanner, staff_target)):
                try:
                    planner = factory(state)
                    check_preparation(state, {kind})
                    if any(item.resolve({}) is None for item in (target(), *cls._caller_targets(state, {kind}))):
                        raise HookError("Personnel integration needs completed native caller imports")
                    planners[kind] = planner
                except Exception as error:
                    failure(kind, error)
            return cls(planners, channel, select, report=report) if planners else None
        except Exception as error:
            failure("personnel", error)
            return None

    def __init__(self, planners, channel, select, *, report=None):
        super().__init__(channel, select, "personnel.contract")
        self.planners = dict(planners)
        self.feedback = None
        try:
            if not self.planners or self.planners.keys() - {"driver", "staff"}:
                raise HookError("Personnel integration needs supported native planners")
            state = next(iter(self.planners.values())).state
            if any(planner.state is not state for planner in self.planners.values()):
                raise HookError("Personnel planners belong to different careers")
            self.callers = PersonnelCallers(self, state)
            signing = [factory() for kind, factory in (("driver", driver_target), ("staff", staff_target))
                       if kind in self.planners]
            callers = self._caller_targets(state, self.planners)
            # Recheck the complete set here: trusted callers may also construct
            # this adapter directly, without the factory's per-kind preflight.
            # Readiness here is local to this signing component. The wider
            # personnel scope stays closed until its transaction paths are mapped.
            self._main()
            if any(target.resolve(self.completed) is None for target in (*signing, *callers)):
                raise HookError("Personnel integration requires its native caller modules to finish loading")
            check_preparation(state, self.planners)
            for target in (*signing, *callers):
                self.approve(target)
            # Check every caller before the first wrapper. Parent journals restore
            # all assignments if installation fails partway through.
            for target in callers:
                Manager.install(self, target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            projection = self._projection
            codec = ResultContract({"shape": "bool", "denied": {"literal": False},
                                    "accepted": {"path": [], "one_of": [True]}})
            for owner in self.subscriptions:
                self.register(owner, [CallBinding(target.name, target.name, projection, codec) for target in signing])
            self.poll()
            if any(group["state"] != "active" for group in self.groups.values()):
                raise HookError("Personnel integration did not install completely")
            self.feedback = PersonnelFeedback(self, state, report or self._report)
        except Exception:
            self.stop()
            raise

    def _projection(self, arguments, result):
        caller = getattr(self._local, "personnel_caller", None)
        # Both signing targets share one projection and dispatch path. Native
        # manager identity selects the planner, never a mod-supplied target name.
        planner = next(planner for planner in self.planners.values() if planner.manager is arguments["self"])
        return regulation_context("personnel.contract", planner.prepare(arguments),
                                  initiator=caller["initiator"] if caller else "unknown")

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("personnel.caller."):
            wrapped = self.callers.wrap(target.name.rsplit(".", 1)[1], original, signature)
            update_wrapper(wrapped, original)
            wrapped._tpm_hook_group = name
            return wrapped
        return super()._wrapper(name, binding, target, original, signature)

    def _validate(self, original, signature, entries, args, kwargs):
        # Class wrappers are shared by every career instance. Other careers must
        # keep their original signing behavior and cannot use this policy set.
        bound = signature.bind(*args, **kwargs)
        if not any(bound.arguments["self"] is planner.manager for planner in self.planners.values()):
            return original(*args, **kwargs)
        caller = getattr(self._local, "personnel_caller", None)
        candidate = caller.get("staff_candidate") if caller else None
        if candidate and bound.arguments.get("staff") is candidate[1]:
            # The copy was only for the caller's temporary field write. Proposal
            # identity, contract assignment and morale effects use the real person.
            bound.arguments["staff"] = candidate[0]
        return super()._validate(original, signature, entries, bound.args, bound.kwargs)

    def _deny(self, codec, outcome):
        caller = getattr(self._local, "personnel_caller", None)
        if caller:
            # Carry the host status through nested cancellation; only native
            # return values and feedback should extract the display message.
            caller["refusal"] = outcome
            caller["normalizations"].clear()
        screen = getattr(self._local, "personnel_screen", None)
        # The optional UI owner records the reason; policy decisions and native
        # return values remain the responsibility of this shared signing guard.
        if self.feedback is not None and screen is not None:
            self.feedback.refuse(screen, outcome.message)
        return super()._deny(codec, outcome)

    def stop(self):
        result = super().stop()
        if self.feedback is not None:
            self.feedback.stop()
        return result

    def poll(self):
        result = super().poll()
        # A screen imported on a worker is installed at the next main-thread poll.
        if self.feedback is not None:
            self.feedback.poll()
        return result

    def _execute(self, original, args, kwargs):
        caller = getattr(self._local, "personnel_caller", None)
        if caller:
            # Publish native getter initialization at the same admission boundary
            # as signing; denial discards it and native failure still retains it.
            publish_preparation(caller)
        previous = getattr(self._local, "committing_personnel", False)
        self._local.committing_personnel = True
        try:
            return original(*args, **kwargs)
        finally:
            self._local.committing_personnel = previous
