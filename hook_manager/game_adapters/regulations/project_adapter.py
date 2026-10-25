# SPDX-License-Identifier: MPL-2.0
"""Install the private development-project integration for one career.

The caller supplies an authenticated channel and native adopted-rule selection.
No mod can select a target, projection, result codec or callable registration.
Other scopes and public capability admission retain their separate release gates.
"""

from functools import update_wrapper

from ...callables import CallBinding, ResultContract
from ...manager import HookError, Manager
from .guards import RegulationGuards
from .contexts import regulation_context
from ..projects.caller_targets import project_caller_targets
from ..projects.callers import ProjectCallers
from ..projects.planner import ProjectPlanner
from ..projects.targets import project_targets


class ProjectAdapter(RegulationGuards):
    @classmethod
    def create(cls, state, channel, select, report):
        try:
            if not channel.bindings("development.project"):
                return None  # No subscribing mods: do not inspect or hook the game.
            # The native planner has no regulation logging/subscription policy.
            # This consumer owns both the skip diagnostic and guard installation.
            return cls(ProjectPlanner(state), channel, select)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED scope=development.project: "
                   + " ".join(str(error).split())[:512] + "; project integration skipped; continuing native game")
            return None

    def __init__(self, planner, channel, select):
        super().__init__(channel, select, "development.project")
        self.planner = planner
        self.callers = ProjectCallers(self, planner.state)
        starts, callers = project_targets(), project_caller_targets()
        try:
            # Preparing only some callers is unsafe: a worker could consume an
            # unwrapped AI caller and write its package before our start guard.
            # The career initializer must wait for normal imports, then create
            # this integration on the main thread. Never force a game import.
            self._main()
            if any(target.resolve(self.completed) is None for target in (*starts, *callers)):
                raise HookError("Project integration requires its native caller modules to finish loading")
            for target in (*starts, *callers):
                self.approve(target)
            # All callers and starts were checked before the first assignment.
            # Reuse the manager's owned journal to roll back an install failure.
            for target in callers:
                Manager.install(self, target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            projections = {target.name: self._projection(target.name) for target in starts}
            codec = ResultContract({"shape": "bool", "denied": {"literal": False},
                                    "accepted": {"path": [], "one_of": [True]}})
            for owner in self.subscriptions:
                self.register(owner, [CallBinding(target.name, target.name, projections[target.name], codec) for target in starts])
            self.poll()
            if any(group["state"] != "active" for group in self.groups.values()):
                raise HookError("Project integration did not install completely")
        except Exception:
            self.stop()
            raise

    def _projection(self, target):
        def project(arguments, result):
            caller = getattr(self._local, "project_caller", None)
            return regulation_context("development.project", self.planner.prepare(target, arguments),
                                      initiator=caller["initiator"] if caller else "unknown")
        return project

    def _proposal_identity(self, arguments):
        """Include future-chassis rule inputs in before/after comparisons."""
        technical = None
        if arguments.get("self") is self.planner.managers[1] and arguments.get("project_type") == "future":
            technical = self.planner.technical.identity()
        return self.policy_identity(), technical

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("projects.caller."):
            wrapped = self.callers.wrap(target.name.rsplit(".", 1)[1], original, signature)
            update_wrapper(wrapped, original)
            wrapped._tpm_hook_group = name
            return wrapped
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

