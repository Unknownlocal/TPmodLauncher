# SPDX-License-Identifier: MPL-2.0
"""Guard academy signing/renewal and their immediate native callers."""

from functools import update_wrapper

from ...callables import CallBinding, refuse_with_reason
from ...manager import HookError, Manager
from ..academy.caller_targets import academy_caller_targets
from ..academy.callers import AcademyCallers
from ..academy.planner import AcademyContractPlanner
from ..academy.preparation_methods import preparation_methods
from ..academy.preparation import copy_random
from ..academy.targets import academy_targets
from .contexts import regulation_context
from .guards import RegulationGuards


class AcademyAdapter(RegulationGuards):
    def __init__(self, state, channel, select):
        """Prepare academy support without assigning native wrappers."""
        super().__init__(channel, select, "personnel.contract")
        targets, callers = academy_targets(), academy_caller_targets()
        self._main()
        self.planner = planner = AcademyContractPlanner(state)
        self.signing, self.native_callers = targets, callers
        resolved = {target.name: target.resolve(self.completed) for target in (*targets, *callers)}
        if any(value is None for value in resolved.values()):
            raise HookError("Academy integration needs completed native caller imports")
        # Check every caller/helper and RNG capability before assigning any
        # wrapper, so incompatibility cannot leave half an integration active.
        for target in callers:
            if target.name.rsplit(".", 1)[1] in ("ai_sign", "ai_renew"):
                copy_random(planner.state, resolved[target.name][2])
        screen = resolved[callers[0].name][0]
        self.callers = AcademyCallers(self, planner.state, preparation_methods(planner.state, screen))
        for target in (*targets, *callers):
            self.approve(target)

    def install_prepared(self):
        """Assign academy wrappers after all personnel support is compatible."""
        try:
            self._main()
            for target in self.native_callers:
                Manager.install(self, target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            projections = {target.name: self._projection(target.name) for target in self.signing}
            for owner in self.subscriptions:
                self.register(owner, [CallBinding(target.name, target.name, projections[target.name], refuse_with_reason)
                                      for target in self.signing])
            self.poll()
            if any(group["state"] != "active" for group in self.groups.values()):
                raise HookError("Academy integration did not install completely")
            return self
        except Exception:
            self.stop()
            raise

    def _projection(self, target):
        def project(arguments, result):
            caller = getattr(self._local, "academy_caller", None)
            return regulation_context("personnel.contract", self.planner.prepare(target, arguments),
                                      initiator=caller["initiator"] if caller else "unknown")
        return project

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("academy.caller."):
            wrapped = self.callers.wrap(target.name.rsplit(".", 1)[1], original, signature)
            update_wrapper(wrapped, original)
            wrapped._tpm_hook_group = name
            return wrapped
        return super()._wrapper(name, binding, target, original, signature)

    def _validate(self, original, signature, entries, args, kwargs):
        bound = signature.bind(*args, **kwargs)
        if bound.arguments["self"] is not self.planner.manager:
            return original(*args, **kwargs)
        caller = getattr(self._local, "academy_caller", None)
        if caller:
            caller["attempted"], caller["team_name"] = True, bound.arguments["team_name"]
        return super()._validate(original, signature, entries, args, kwargs)

    def _deny(self, refuse, outcome):
        caller = getattr(self._local, "academy_caller", None)
        if caller:
            # Retain the refusal's status even if later AI attempts replace the
            # thread's latest outcome. This frame still has preparation to drop.
            caller["refusal"] = outcome
            if caller["preparation"] is not None:
                caller["preparation"].refused = True
        return super()._deny(refuse, outcome)

    def _execute(self, original, args, kwargs):
        caller = getattr(self._local, "academy_caller", None)
        preparation = caller["preparation"] if caller else None
        if preparation:
            # This boundary is reached only after every subscribed validator
            # allows. Publish caller setup before native sign/renew runs once.
            preparation.publish(caller["team_name"])
        try:
            return original(*args, **kwargs)
        finally:
            if preparation:
                # Native refusal/exception may still have performed native setup;
                # the AI view must reflect that outcome without retrying the call.
                preparation.refresh(caller["team_name"])
