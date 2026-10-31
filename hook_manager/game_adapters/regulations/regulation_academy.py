# SPDX-License-Identifier: MPL-2.0
"""Guard academy signing/renewal and their immediate native callers."""

from ...callables import CallBinding, refuse_with_reason
from ...manager import HookError
from ..academy.caller_targets import academy_caller_targets
from ..academy.callers import AcademyCallers
from ..academy.planner import AcademyContractPlanner
from ..academy.preparation_methods import preparation_methods
from ..academy.preparation import copy_random
from ..academy.targets import academy_targets
from .contexts import regulation_context
from .guards import RegulationGuards


class RegulationAcademyAdapter(RegulationGuards):
    def __init__(self, state, channel, select, *, submit_log=None):
        """Prepare academy support without assigning native wrappers."""
        super().__init__(channel, select, "personnel.contract", submit_log=submit_log)
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
        with self._installation():
            self._install_groups(self.native_callers, [
                CallBinding(target.name, target.name, self._projection(target.name), refuse_with_reason)
                for target in self.signing])
        return self

    def _projection(self, target):
        def project(arguments, result):
            caller = getattr(self._local, "academy_caller", None)
            # _decision checks and copies this whole context before dispatch.
            prepared = self.planner._build_proposal(target, arguments)
            if "employer_payroll" in getattr(self._local, "inputs", ()):
                # Academy fees are separate from team employment salaries.
                prepared["proposal"]["employer"]["payroll"] = None
            return regulation_context("personnel.contract", prepared,
                                      initiator=caller["initiator"] if caller else "unknown")
        return project

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("academy.caller."):
            return self._caller_wrapper(name, self.callers, target, original, signature)
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
