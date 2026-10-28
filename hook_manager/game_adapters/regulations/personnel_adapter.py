# SPDX-License-Identifier: MPL-2.0
"""Guard Formula driver/staff signing with one shared negotiation owner."""

from functools import update_wrapper
from contextlib import nullcontext
import inspect
import threading

from ...callables import CallBinding, refuse_boolean
from ...manager import HookError, Manager
from .guards import RegulationGuards
from .contexts import regulation_context
from .outcome import ValidationOutcome
from .refused_proposals import RefusedProposals
from ..personnel.caller_targets import personnel_caller_targets, personnel_mail_target
from ..personnel.callers import PersonnelCallers
from ..personnel.offer import PersonnelOffer
from ..personnel.feedback import PersonnelFeedback
from ..drivers.planner import DriverContractPlanner
from ..drivers.query_inputs import discard_query_inputs
from ..personnel.preparation import publish_preparation
from ..personnel.preparation_methods import check_preparation
from ..drivers.targets import driver_target
from ..staff.staffing import StaffingPlanner
from ..staff.targets import staff_target, staffing_targets
from .staffing_coordinator import StaffingCoordinator
from .native_rule_selection import NativeRuleSelection


class PersonnelAdapter(RegulationGuards):
    def __init__(self, state, channel, select, *, report=None):
        """Check all signing/staffing support without assigning native wrappers."""
        super().__init__(channel, select, "personnel.contract", report=report)
        # Reserve career inputs through native writes, not just registry access.
        self._personnel_operation = threading.Lock()
        self.feedback = None
        self.report = report or self._report
        self._main()
        staffing = StaffingPlanner(state)
        # Reuse staffing's checked staff planner instead of checking it twice.
        self.planners = {"driver": DriverContractPlanner(state), "staff": staffing.contracts}
        self.staffing = StaffingCoordinator(self, staffing)
        self.callers = PersonnelCallers(self, state)
        self.signing = (driver_target(), staff_target())
        self.native_callers = (*personnel_caller_targets(), *staffing_targets())
        self.offers = PersonnelOffer(state, self.planners)
        # Headless careers may omit mail. A real mailbox must be covered.
        if getattr(state, "email_manager", None) is not None:
            self.native_callers += (personnel_mail_target(),)
        for target in (*self.signing, *self.native_callers):
            resolved = target.resolve(self.completed)
            if resolved is None:
                raise HookError("Personnel integration requires completed native caller imports")
            if target.name.startswith("staffing.") and (
                    inspect.getattr_static(state.staff_manager, target.attribute) is not resolved[2]):
                # An instance/subclass override would evade the mapped class.
                raise HookError("Staffing integration needs the mapped native entry points")
            self.approve(target)
        check_preparation(state)

    def install_prepared(self):
        """Install only after the whole personnel group passes compatibility checks."""
        try:
            self._main()
            for target in self.native_callers:
                Manager.install(self, target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            projection = self._projection
            for owner in self.subscriptions:
                self.register(owner, [CallBinding(target.name, target.name, projection, refuse_boolean) for target in self.signing])
            self.poll()
            if any(group["state"] != "active" for group in self.groups.values()):
                raise HookError("Personnel integration did not install completely")
            self.feedback = PersonnelFeedback(self, self.planners["driver"].state, self.report)
            return self
        except Exception:
            self.stop()
            raise

    def _projection(self, arguments, result):
        caller = getattr(self._local, "personnel_caller", None)
        # Both signing targets share one projection and dispatch path. Native
        # manager identity selects the planner, never a mod-supplied target name.
        planner = next(planner for planner in self.planners.values() if planner.manager is arguments["self"])
        context = regulation_context("personnel.contract", planner.prepare(arguments),
                                     initiator=caller["initiator"] if caller else "unknown")
        self._local.native_check_season = context["effective"]["season"]
        return context

    def native_check_scope(self, start_week):
        """Share an offer's explicit date with the native check switches."""
        return NativeRuleSelection.scope(next(iter(self.planners.values())).state, start_week=start_week)

    def validate_submission(self, prepare, execute, *, initiator):
        """Check offered terms without granting permission for a later signing."""
        if not self._personnel_operation.acquire(blocking=False):
            return ValidationOutcome("unavailable", "Personnel operation is busy"), None
        try:
            context = None

            def project():
                nonlocal context
                context = regulation_context(self.scope, prepare(), initiator=initiator, operation="preview")
                return context

            outcome = self._decision(project, set(self.subscriptions))
            self._local.outcome = outcome
            if outcome.status != "allow":
                return outcome, None
            # Native submission prechecks must see the same season as the mods.
            with self.native_check_scope(context["effective"]["start_week"]):
                return outcome, execute()
        finally:
            self._personnel_operation.release()

    def _refusal(self, context, selections, calls, deadline):
        """Keep duplicate-denial handling inside this caller's operation lifetime."""
        # Staffing validates prepared contracts without entering a live signing
        # wrapper, so its coordinator supplies the same bounded history frame.
        caller = (getattr(self._local, "personnel_caller", None)
                  or getattr(self._local, "staffing_caller", None))
        if caller is None:
            # Direct signing has no enclosing operation to share earlier checks.
            return super()._refusal(context, selections, calls, deadline)
        history = caller["validation"]
        if "refusals" not in history:
            # Native caller support supplies only storage; regulation decisions
            # and policy revision checks remain in this integration.
            history["refusals"] = RefusedProposals()
        return history["refusals"].check(self.channel, context, selections, calls, deadline, super()._refusal)

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("staffing."):
            wrapped = self.staffing.wrap(target.name.rsplit(".", 1)[1], original, signature)
            update_wrapper(wrapped, original)
            wrapped._tpm_hook_group = name
            return wrapped
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
        # Reserve before projection and keep the reservation through native
        # execution. A staffing publication must not invalidate allowed terms.
        # Busy is host unavailability, so no healthy validator is latched failed.
        if not self._personnel_operation.acquire(blocking=False):
            return self._deny(entries[0][1].refuse, ValidationOutcome("unavailable", "Personnel operation is busy"))
        previous = getattr(self._local, "native_check_season", None)
        try:
            return super()._validate(original, signature, entries, bound.args, bound.kwargs)
        finally:
            self._local.native_check_season = previous
            self._personnel_operation.release()

    def _deny(self, refuse, outcome):
        caller = getattr(self._local, "personnel_caller", None)
        if caller:
            # Carry the host status through nested cancellation; only native
            # return values and feedback should extract the display message.
            caller["refusal"] = outcome
            caller["normalizations"].clear()
            discard_query_inputs(caller)
        screen = getattr(self._local, "personnel_screen", None)
        # The optional UI owner records the reason; policy decisions and native
        # return values remain the responsibility of this shared signing guard.
        if self.feedback is not None and screen is not None:
            self.feedback.refuse(screen, outcome.message)
        return super()._deny(refuse, outcome)

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
            # Signing can change career data beyond the proposal, even if it
            # ultimately returns False or raises. Earlier refusals must expire.
            caller["validation"].clear()
            discard_query_inputs(caller)
            # Publish native getter initialization at the same admission boundary
            # as signing; denial discards it and native failure still retains it.
            publish_preparation(caller)
        previous = getattr(self._local, "committing_personnel", False)
        self._local.committing_personnel = True
        try:
            # Keep actual signing on the same season used by mod validation,
            # even when a future rule differs from today's choice. Prepared
            # native work without a new projection retains its enclosing scope.
            season = getattr(self._local, "native_check_season", None)
            scope = (NativeRuleSelection.scope(next(iter(self.planners.values())).state, season)
                     if season is not None else nullcontext())
            with scope:
                return original(*args, **kwargs)
        finally:
            self._local.committing_personnel = previous
            if caller:
                # Nested native calls may have added refusals during execution.
                # Discard them after the signer's last possible state changes.
                caller["validation"].clear()
                discard_query_inputs(caller)
