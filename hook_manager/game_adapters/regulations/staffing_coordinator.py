# SPDX-License-Identifier: MPL-2.0
"""Validate a complete private staff operation before publishing its native writes."""

import threading
from contextlib import contextmanager

from ...manager import HookError
from ..personnel.cancellation import PersonnelCancellation
from .contexts import regulation_context
from .outcome import ValidationOutcome
from .staffing_result import StaffingResult


class StaffingCoordinator:
    """Validate native onboarding/recovery on the main thread or an admitted worker.

    The planner owns private native effects; this class owns regulation decisions
    and the final permission to publish. Its fixed native entry wrappers cover
    only newly proposed staffing contracts; loading and activation of accepted
    contracts remain native. Workers share the active staff signing reservation.
    """

    def __init__(self, guards, planner):
        # The retained signing function must run on the private manager. Keeping
        # an installed live wrapper here would validate against the wrong owner.
        staff = guards.planners.get("staff")
        if (staff is None or staff.manager is not planner.manager or staff.state is not planner.state
                or getattr(planner.sign, "_tpm_hook_group", None) is not None):
            raise HookError("Staffing coordinator needs matching pre-hook native staff planning")
        self.guards, self.planner = guards, planner

    @contextmanager
    def _caller_operation(self):
        """Admit a native worker only for its fixed, owned staffing entry call."""
        guards, current = self.guards, threading.current_thread()
        if current is threading.main_thread() or getattr(guards._local, "operation", False):
            yield
            return
        # These wrappers are trusted admission points, not mod-selected targets.
        # Register the actual Thread object before operation() and revoke only
        # our temporary grant. An earlier enrolment belongs to its existing owner.
        with guards._lock:
            already = current in guards._threads
            guards._threads.add(current)
        try:
            with guards.operation():
                yield
        finally:
            if not already:
                with guards._lock:
                    guards._threads.discard(current)

    def wrap(self, role, original, signature):
        """Keep native list/None returns while discarding an unapproved batch."""
        def wrapped(*args, **kwargs):
            guards = self.guards
            bound = signature.bind(*args, **kwargs)
            bound.apply_defaults()
            if (guards.closed or bound.arguments["self"] is not self.planner.manager
                    or not any(binding.target == "staff.sign" for binding in guards.entries.values())):
                # Other careers, private manager copies and explicitly removed
                # staff bindings use native code, including its usual arguments.
                return original(*args, **kwargs)
            with self._caller_operation():
                result = (self.onboard(bound.arguments["team_name"], contract_years=bound.arguments["contract_years"])
                          if role == "onboard" else self.recover(bound.arguments["roles"]))
            if result.published:
                return result.native_result
            # Native recovery ignores sign_staff False and itself returns None.
            # Never publish the successful prefix or leak its private name list.
            guards._report(result.refusal.message if result.refusal else "Native staffing left required roles unfilled")
            return [] if role == "onboard" else None
        return wrapped

    def onboard(self, team_name, *, contract_years=1):
        """Validate and publish the native joining-team staff operation."""
        return self._run(self.planner.prepare_onboarding, team_name, contract_years=contract_years)

    def recover(self, roles=None):
        """Validate and publish native missing-staff recovery for these roles."""
        return self._run(self.planner.prepare_recovery, roles)

    def _policy(self, owners):
        """Read acknowledged policy identities while the caller holds the channel lock."""
        guards, channel = self.guards, self.guards.channel
        if not owners:
            return ()
        if (channel.closed or not getattr(channel, "validation_ready", True)
                or owners & channel._unavailable or not owners <= channel._generations.keys()
                or any(guards.owners.get(owner) != "ready" for owner in owners)):
            return None
        try:
            selection = guards.policy_identity()
        except Exception as error:
            # Unsupported native data must refuse staffing before planning or
            # publication, rather than escape as an exception in the game worker.
            guards._report(error)
            return None
        return selection, channel.epoch, tuple((owner, channel._generations[owner], channel._owners[owner]["revision"])
                                    for owner in owners)

    def _run(self, prepare, *args, **kwargs):
        """Reserve one staffing run while retaining its bindings and caller frame."""
        guards = self.guards
        guards._local.outcome = None
        with guards._lock:
            if guards.closed:
                raise HookError("Staffing coordinator is closed")
            current = threading.current_thread()
            # Enrollment grants this particular Thread object; operation() marks
            # its admitted work. Both are needed, while main-thread runs retain
            # the existing idle-operation check.
            if current is threading.main_thread():
                guards._main()
            elif current not in guards._threads or not getattr(guards._local, "operation", False):
                raise HookError("Staffing requires an enrolled native worker operation")
            if (getattr(guards._local, "staffing_caller", None) is not None
                    or getattr(guards._local, "dispatching", False)):
                raise HookError("Nested staffing preparation is not supported")
            # Admission declarations survive explicit owner removal. Use only
            # installed staff bindings, then freeze configuration for this run.
            participants = frozenset(owner for (owner, _), binding in guards.entries.items()
                                     if binding.target == "staff.sign")
            if current is not threading.main_thread() and not participants:
                # Owner removal restores unwrapped native signing. A worker can
                # no longer exclude those writes, so stop before taking a snapshot
                # or adding a coordinator frame/count to its enclosing operation.
                outcome = ValidationOutcome("unavailable", "Native staff signing interception is unavailable")
                guards._local.outcome = outcome
                return StaffingResult(False, refusal=outcome)
            guards._active += 1
        # Reuse policy refusals only within this operation. No allowance survives
        # a signing attempt; every later allowed contract is checked freshly.
        frame, refusals = {"validation": {}}, []
        previous = getattr(guards._local, "staffing_caller", None)
        guards._local.staffing_caller = frame
        channel = guards.channel

        def unavailable(message="Validation unavailable", plan=None):
            outcome = ValidationOutcome("unavailable", message)
            guards._local.outcome = outcome
            return StaffingResult(False, plan.native_result if plan else None,
                                  plan.missing if plan else (), outcome)

        def validate(prepared):
            # Validation grants private native execution, not live publication.
            # After the last owner is removed, the channel is no longer required.
            outcome = (guards._decision(lambda: regulation_context("personnel.contract", prepared, initiator="system"),
                                        participants) if participants else ValidationOutcome("allow"))
            guards._local.outcome = outcome
            if outcome.status == "unavailable":
                raise PersonnelCancellation(outcome)
            if outcome.status == "deny":
                refusals.append(outcome)
                return False
            frame["validation"].clear()
            return True

        acquired = False
        try:
            # This reservation is shared with live signing and starts before
            # reading/copying native inputs, not just at final publication. Other
            # attempts fail promptly; the registry lock stays free during dispatch.
            acquired = guards._personnel_operation.acquire(blocking=False)
            if not acquired:
                return unavailable("Personnel operation is busy")
            # Do not hold the transport lock across mod dispatch. Remember its
            # acknowledged policy now, then require the same policy at publication.
            if participants and not channel._lock.acquire(blocking=False):
                return unavailable()
            try:
                policy = self._policy(participants)
            finally:
                if participants:
                    channel._lock.release()
            if policy is None:
                return unavailable()
            try:
                plan = prepare(*args, validate=validate, **kwargs)
            except PersonnelCancellation as refusal:
                return StaffingResult(False, refusal=refusal.outcome)
            if plan.missing:
                # Native recovery ignores signing False. Its None return cannot
                # prove completion: check the roles native code actually queried.
                return StaffingResult(False, plan.native_result, plan.missing, refusals[0] if refusals else None)
            if participants and not channel._lock.acquire(blocking=False):
                return unavailable(plan=plan)
            try:
                if policy != self._policy(participants):
                    return unavailable("Staffing policy changed during preparation", plan)
                try:
                    # Policy and native inputs are checked before any live write.
                    # Signing is not replayed, so native choices/effects run once.
                    plan.publication.publish(plan.generated, plan.history_entries)
                except HookError as error:
                    return unavailable(str(error), plan)
            finally:
                if participants:
                    channel._lock.release()
            return StaffingResult(True, plan.native_result)
        finally:
            # Every denial, cancellation, stale plan and native exception returns
            # this reservation. Busy attempts never release another run's lock.
            if acquired:
                guards._personnel_operation.release()
            guards._local.staffing_caller = previous
            with guards._lock:
                guards._active -= 1
