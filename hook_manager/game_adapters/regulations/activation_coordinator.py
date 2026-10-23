# SPDX-License-Identifier: MPL-2.0
"""Validate every due agreement before running native employment activation."""

from ...manager import HookError
from ..personnel.activation_methods import check_activation_execution
from ..personnel.activation_snapshot import ActivationSnapshot
from .contexts import regulation_context
from .outcome import ValidationOutcome


class ActivationCoordinator:
    """Check the complete due set before native activation on the main thread.

    This does not advance the week or install a career hook. An enclosing caller
    must use it before its own mutations and stop when activation is refused.
    """

    def __init__(self, guards, planner):
        if any(kind not in guards.planners or guards.planners[kind].state is not planner.state
               or guards.planners[kind].manager is not planner.managers[kind] for kind in planner.kinds):
            raise HookError("Activation coordinator needs matching native personnel planners")
        check_activation_execution(planner.state, planner.kinds)
        self.guards, self.planner = guards, planner

    def _policy(self, owners):
        """Read acknowledged policy identities while the channel lock is held."""
        guards, channel = self.guards, self.guards.channel
        if not owners:
            return ()
        if (channel.closed or owners & channel._unavailable or not owners <= channel._generations.keys()
                or any(guards.owners.get(owner) != "ready" for owner in owners)):
            return None
        return channel.epoch, tuple((owner, channel._generations[owner], channel._owners[owner]["revision"])
                                    for owner in owners)

    def activate(self, current_week, *, evaluation_season=None):
        """Return True after native activation, including a successful no-op.

        False leaves activation untouched; last_outcome distinguishes denial
        from unavailable checks. Native exceptions propagate once and retain
        their native effects rather than becoming validation failures.
        """
        guards, planner = self.guards, self.planner
        guards._local.outcome = None
        with guards._lock:
            if guards.closed:
                raise HookError("Activation coordinator is closed")
            # Worker activation needs separate enclosing admission work. Merely
            # enrolling a thread cannot make this main-thread entry point usable.
            guards._main()
            # Subscriptions describe admission; installed bindings describe who
            # still participates after owner removal. Pin that set for this batch.
            participants = frozenset(owner for (owner, _), binding in guards.entries.items()
                                     if binding.target in {kind + ".sign" for kind in planner.kinds})
            guards._active += 1
        channel, acquired = guards.channel, False

        def refuse(message):
            guards._local.outcome = ValidationOutcome("unavailable", message)
            return False

        try:
            # Share signing/staffing's reservation from the first native read
            # through the last native effect. A busy attempt owns nothing to release.
            acquired = guards._personnel_operation.acquire(blocking=False)
            if not acquired:
                return refuse("Personnel operation is busy")
            if participants and not channel._lock.acquire(blocking=False):
                return refuse("Validation unavailable")
            try:
                policy = self._policy(participants)
            finally:
                if participants:
                    channel._lock.release()
            if policy is None:
                return refuse("Validation unavailable")
            try:
                # Capture before copying, so a copy-time change cannot become a
                # newer baseline for already prepared agreements.
                snapshot = ActivationSnapshot(planner, evaluation_season)
                proposals = planner.prepare(current_week, evaluation_season=evaluation_season)
            except HookError as error:
                return refuse(str(error))
            # No channel lock spans guest dispatch. In particular, a later staff
            # denial must be known before any earlier driver agreement activates.
            for activation in proposals:
                outcome = (guards._decision(lambda: regulation_context("personnel.contract", activation.prepared,
                                           initiator="system", operation="activate"), participants)
                           if participants else ValidationOutcome("allow"))
                guards._local.outcome = outcome
                if outcome.status != "allow":
                    return False
            if participants and not channel._lock.acquire(blocking=False):
                return refuse("Validation unavailable")
            try:
                # Publication/reset uses this same channel lock. The batch needs
                # the policy that allowed every proposal, not a mix of revisions.
                if policy != self._policy(participants):
                    return refuse("Activation policy changed during validation")
                try:
                    snapshot.check()
                except HookError as error:
                    return refuse(str(error))
                guards._local.outcome = ValidationOutcome("allow")
                # Keep native execution outside validation-error handling. These
                # bodies own normalization, setters, caches and career callbacks;
                # failure after a native write must not trigger a retry or rollback.
                for kind in planner.kinds:
                    function, static = planner.methods["state"]["_activate_future_" + kind + "_contracts"]
                    if static:
                        function(current_week)
                    else:
                        function(planner.state, current_week)
            finally:
                if participants:
                    channel._lock.release()
            return True
        finally:
            if acquired:
                guards._personnel_operation.release()
            with guards._lock:
                guards._active -= 1
