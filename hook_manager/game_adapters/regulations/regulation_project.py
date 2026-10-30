# SPDX-License-Identifier: MPL-2.0
"""Install the private development-project integration for one career.

The caller supplies an authenticated channel and native adopted-rule selection.
No mod can select a target, projection, result codec or callable registration.
"""

from ...diagnostics import build_log_record

from ...callables import CallBinding, refuse_boolean
from ...manager import HookError
from .guards import RegulationGuards
from .contexts import regulation_context
from ..projects.caller_targets import project_caller_targets
from ..projects.callers import ProjectCallers
from ..projects.planner import ProjectPlanner
from ..projects.targets import project_targets


class RegulationProjectAdapter(RegulationGuards):
    @classmethod
    def create(cls, state, channel, select, submit_log):
        try:
            if not channel.bindings("development.project"):
                return None  # No subscribing mods: do not inspect or hook the game.
            # The native planner has no regulation logging/subscription policy.
            # This consumer owns both the skip diagnostic and guard installation.
            return cls(ProjectPlanner(state), channel, select, submit_log=submit_log)
        except Exception as error:
            submit_log(build_log_record('WARNING', "REGULATIONS COMPATIBILITY FAILED scope=development.project: "
                   + str(error) + "; project integration skipped; continuing native game", error=error, component="regulations"))
            return None

    def __init__(self, planner, channel, select, *, submit_log=None):
        super().__init__(channel, select, "development.project", submit_log=submit_log)
        self.planner = planner
        self.callers = ProjectCallers(self, planner.state)
        starts, callers = project_targets(), project_caller_targets()
        with self._installation():
            # Preparing only some callers is unsafe: a worker could consume an
            # unwrapped AI caller and write its package before our start guard.
            # The career initializer must wait for normal imports, then create
            # this integration on the main thread. Never force a game import.
            if any(target.resolve(self.completed) is None for target in (*starts, *callers)):
                raise HookError("Project integration requires its native caller modules to finish loading")
            for target in (*starts, *callers):
                self.approve(target)
            self._install_groups(callers, [
                CallBinding(target.name, target.name, self._projection(target.name), refuse_boolean)
                for target in starts])

    def _projection(self, target):
        def project(arguments, result):
            caller = getattr(self._local, "project_caller", None)
            return regulation_context("development.project", self.planner.prepare(target, arguments),
                                      initiator=caller["initiator"] if caller else "unknown")
        return project

    def _validate(self, original, signature, entries, args, kwargs):
        """Apply this career's checks only to its own aero and chassis managers."""
        # Class hooks also see other careers; leave their calls entirely native.
        bound = signature.bind(*args, **kwargs)
        if not any(bound.arguments["self"] is manager for manager in self.planner.managers):
            return original(*args, **kwargs)
        return super()._validate(original, signature, entries, args, kwargs)

    def _proposal_identity(self, arguments):
        """Include future-chassis rule inputs in before/after comparisons."""
        technical = None
        if arguments.get("self") is self.planner.managers[1] and arguments.get("project_type") == "future":
            technical = self.planner.technical.identity()
        return self.policy_identity(), technical

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("projects.caller."):
            return self._caller_wrapper(name, self.callers, target, original, signature)
        return super()._wrapper(name, binding, target, original, signature)

    def _execute(self, original, args, kwargs):
        self._local.committing_project = True
        try:
            return original(*args, **kwargs)
        finally:
            self._local.committing_project = False
            caller = getattr(self._local, "project_caller", None)
            if caller:
                caller["parts"].clear()

