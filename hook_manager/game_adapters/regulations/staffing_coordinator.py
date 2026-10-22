# SPDX-License-Identifier: MPL-2.0
"""Validate a complete private staff operation before publishing its native writes."""

from ...manager import HookError
from ..personnel.cancellation import PersonnelCancellation
from .contexts import regulation_context
from .outcome import ValidationOutcome
from .staffing_result import StaffingResult


class StaffingCoordinator:
    """Validate native onboarding/recovery as a whole on the main thread.

    The planner owns private native effects; this class owns regulation decisions
    and the final permission to publish. Enclosing career/load/rollover changes
    still need their own integration before using this coordinator.
    """

    def __init__(self, guards, planner):
        # The retained signing function must run on the private manager. Keeping
        # an installed live wrapper here would validate against the wrong owner.
        staff = guards.planners.get("staff")
        if (staff is None or staff.manager is not planner.manager or staff.state is not planner.state
                or getattr(planner.sign, "_tpm_hook_group", None) is not None):
            raise HookError("Staffing coordinator needs matching pre-hook native staff planning")
        self.guards, self.planner = guards, planner

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
        if (channel.closed or owners & channel._unavailable or not owners <= channel._generations.keys()
                or any(guards.owners.get(owner) != "ready" for owner in owners)):
            return None
        return channel.epoch, tuple((owner, channel._generations[owner], channel._owners[owner]["revision"])
                                    for owner in owners)

    def _run(self, prepare, *args, **kwargs):
        guards = self.guards
        guards._local.outcome = None
        with guards._lock:
            guards._main()
            if guards.closed:
                raise HookError("Staffing coordinator is closed")
            # Admission declarations survive explicit owner removal. Use only
            # installed staff bindings, then freeze configuration for this run.
            participants = frozenset(owner for (owner, _), binding in guards.entries.items()
                                     if binding.target == "staff.sign")
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

        try:
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
            guards._local.staffing_caller = previous
            with guards._lock:
                guards._active -= 1
