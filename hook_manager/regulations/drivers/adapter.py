# SPDX-License-Identifier: MPL-2.0
"""Install private Formula driver signing guards for one career."""

from functools import update_wrapper

from ...callables import CallBinding, ResultContract
from ...manager import HookError, Manager
from ..guards import RegulationGuards
from .caller_targets import driver_caller_targets
from .callers import DriverCallers
from .planner import DriverContractPlanner
from .preparation import publish_preparation
from .preparation_methods import check_preparation
from .targets import driver_target


class DriverAdapter(RegulationGuards):
    @classmethod
    def create(cls, state, channel, select, report):
        try:
            # Disabled/unsubscribed mods must not trigger inspection or hooks.
            if not channel.bindings("personnel.contract"):
                return None
            planner = DriverContractPlanner.create(state, report)
            return None if planner is None else cls(planner, channel, select)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED scope=personnel.contract driver: "
                   + " ".join(str(error).split())[:512] + "; driver integration skipped; continuing native game")
            return None

    def __init__(self, planner, channel, select):
        super().__init__(channel, select, "personnel.contract")
        self.planner = planner
        self.callers = DriverCallers(self, planner.state)
        signing, callers = driver_target(), driver_caller_targets()
        try:
            # Readiness here is local to this signing component. The wider
            # personnel scope stays closed until its transaction paths are mapped.
            self._main()
            if any(target.resolve(self.completed) is None for target in (signing, *callers)):
                raise HookError("Driver integration requires its native caller modules to finish loading")
            check_preparation(planner.state)
            for target in (signing, *callers):
                self.approve(target)
            # Check every caller before the first wrapper. Parent journals restore
            # all assignments if installation fails partway through.
            for target in callers:
                Manager.install(self, target.name, [{"target": target.name, "callback": "internal", "phase": "before"}])
            projection = self._projection
            codec = ResultContract({"shape": "bool", "denied": {"literal": False},
                                    "accepted": {"path": [], "one_of": [True]}})
            for owner in self.subscriptions:
                self.register(owner, [CallBinding(signing.name, signing.name, projection, codec)])
            self.poll()
            if any(group["state"] != "active" for group in self.groups.values()):
                raise HookError("Driver integration did not install completely")
        except Exception:
            self.stop()
            raise

    def _projection(self, arguments, result):
        caller = getattr(self._local, "driver_caller", None)
        return self.planner.prepare(arguments, initiator=caller["initiator"] if caller else "unknown")

    def _wrapper(self, name, binding, target, original, signature):
        if target.name.startswith("regulations.driver.caller."):
            wrapped = self.callers.wrap(target.name.rsplit(".", 1)[1], original, signature)
            update_wrapper(wrapped, original)
            wrapped._tpm_hook_group = name
            return wrapped
        return super()._wrapper(name, binding, target, original, signature)

    def _validate(self, original, signature, entries, args, kwargs):
        # Class wrappers are shared by every career instance. Other careers must
        # keep their original signing behavior and cannot use this policy set.
        if signature.bind(*args, **kwargs).arguments["self"] is not self.planner.manager:
            return original(*args, **kwargs)
        return super()._validate(original, signature, entries, args, kwargs)

    def _deny(self, codec, message):
        caller = getattr(self._local, "driver_caller", None)
        if caller:
            caller["refusal"] = message
            caller["normalizations"].clear()
        return super()._deny(codec, message)

    def _execute(self, original, args, kwargs):
        caller = getattr(self._local, "driver_caller", None)
        if caller:
            # Publish native getter initialization at the same admission boundary
            # as signing; denial discards it and native failure still retains it.
            publish_preparation(caller)
        previous = getattr(self._local, "committing_driver", False)
        self._local.committing_driver = True
        try:
            return original(*args, **kwargs)
        finally:
            self._local.committing_driver = previous
