# SPDX-License-Identifier: MPL-2.0
"""Connect team approaches to declared validators; native UI owns refusals."""

import threading
from contextlib import nullcontext

from ...callables import CallBinding
from ...manager import HookError
from ..personnel.approach import PersonnelApproach
from .contexts import regulation_context
from .guards import RegulationGuards


class ApproachAdapter(RegulationGuards):
    @classmethod
    def prepare(cls, state, channel, select, report):
        if not channel.bindings("personnel.approach"):
            return None
        try:
            return cls(PersonnelApproach(state), channel, select, report)
        except Exception as error:
            report("REGULATIONS COMPATIBILITY FAILED scope=personnel.approach: "
                   + " ".join(str(error).split())[:512] + "; approach integration skipped")
            return None

    def __init__(self, approach, channel, select, report):
        super().__init__(channel, select, "personnel.approach", report=report)
        self.approach = approach
        self.personnel = None
        if approach.target.resolve(self.completed) is None:
            raise HookError("Approach integration requires completed native imports")
        self.approve(approach.target)

    def install_prepared(self):
        projection = self._projection
        try:
            for owner in self.subscriptions:
                self.register(owner, [CallBinding("approach", self.approach.target.name,
                                                 projection, self.approach.refuse)])
            if any(value != "ready" for value in self.owners.values()):
                raise HookError("Approach integration did not install completely")
            return self
        except BaseException:
            self.stop()
            raise

    def _projection(self, arguments, result):
        # Shared negotiation callers can use their owned working manager.
        # Describe the real career while leaving native execution on that draft.
        arguments["self"] = self.approach.manager
        return regulation_context(self.scope, self.approach.prepare(arguments),
                                  initiator=arguments["initiated_by"])

    def _validate(self, original, signature, entries, args, kwargs):
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        caller = getattr(self.personnel._local, "personnel_caller", None) if self.personnel else None
        if caller is not None and values["self"] is caller["market"].manager:
            values["self"] = self.approach.manager
        if not self.approach.belongs(values):
            return original(*args, **kwargs)
        refusal = self.approach.input_refusal(values)
        if refusal:
            return self.approach.refuse(refusal)
        # This fixed boundary runs before any native approach mutation, so its
        # worker calls can be admitted directly. No AI-wide wrappers are needed.
        worker = threading.current_thread() is not threading.main_thread()
        with self.operation() if worker else nullcontext():
            return super()._validate(original, signature, entries, args, kwargs)
