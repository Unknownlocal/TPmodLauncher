# SPDX-License-Identifier: MPL-2.0
"""Bind and call regulation handlers through dedicated private runner exports."""

import math
import time

from launcher.sandbox import Limits, SandboxError
from mod_runtime._regulations.policy import decode, decision, encode, fields, invocation
from mod_runtime.runtime import PythonRunner, SOURCE_FUEL, SOURCE_SECONDS
from .batching import batches

# JSON decoding, bounds checks and up to four independent copies are framework
# work, not validator execution. Keep it bounded separately so ordinary accepted
# policy data cannot consume a handler's allowance before the function is called.
PREPARATION_FUEL = 64_000_000


class HandlerRunner:
    """Own a binding table for one activated source runner on its creating thread.

    Failure closes the runner and cannot withdraw required validation. The outer
    regulation session retains the unavailable owner until explicit shutdown.
    Binding uses the source-import fuel/time allowance, separately from ordinary
    validation's ordinary 5M fuel per handler and shared 100 ms action deadline.
    """

    def __init__(self, runner, validators):
        self.runner, self.validators = runner, validators
        self._validation = None

    def validation(self):
        """Trusted additive seam; existing callers keep evaluate() until cutover."""
        if self._validation is None:
            from .validation.runner import ValidationRunner
            self._validation = ValidationRunner(self)
        return self._validation

    @classmethod
    def bind(cls, runner, validators):
        try:
            if (type(runner) is not PythonRunner or not runner._active or runner.closed
                    or runner.manifest.api != "0.4.0" or "regulations" not in runner.manifest.capabilities):
                raise SandboxError("Expected an activated admitted regulation source runner")
            if type(validators) is not list or len(validators) > 32:
                raise ValueError("Invalid regulation binding count")
            ordered = sorted(validators, key=lambda row: row["id"])
            if len({row["id"] for row in ordered}) != len(ordered):
                raise ValueError("Duplicate regulation validator ID")
            declarations = []
            for row in ordered:
                fields(row, ("id", "rule", "scope", "handler"))
                declarations.append({key: row[key] for key in ("rule", "scope", "handler")})
            with runner.sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                for index, declaration in enumerate(declarations):
                    error = runner.sandbox.call("bind-regulation", index, encode({"owner": runner.manifest.id, **declaration}))
                    if error:
                        raise SandboxError(("Regulation binding failed: " + error)[:512])
            return cls(runner, {row["id"]: (index, declaration) for index, (row, declaration)
                                in enumerate(zip(ordered, declarations))})
        except BaseException:
            if type(runner) is PythonRunner:
                runner.close()
            raise

    def evaluate(self, calls, deadline):
        try:
            if (type(calls) is not list or not 1 <= len(calls) <= 32
                    or type(deadline) not in (int, float) or not math.isfinite(deadline)):
                raise ValueError("Invalid bounded regulation action")
            deadline = min(deadline, time.monotonic() + 0.1)
            prepared = []
            for call in calls:
                fields(call, ("validator", "context", "regulation"))
                index, declaration = self.validators[call["validator"]]
                context, regulation = invocation(call["context"], call["regulation"])
                if context["scope"] != declaration["scope"] or regulation["key"] != declaration["rule"]:
                    raise ValueError("Foreign regulation validator context or rule")
                prepared.append((index, context, regulation))
            indices = [row[0] for row in prepared]
            if indices != sorted(indices):
                raise ValueError("Regulation validators must follow ascending ID order")
            results = []
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SandboxError("Regulation action deadline expired")
            # One action watchdog covers every chunk. Fresh per-call fuel does
            # not create another timer or extend the original absolute deadline.
            with self.runner.sandbox.phase(fuel=PREPARATION_FUEL, seconds=remaining):
                for packet, group in batches(prepared):
                    if packet is None:
                        # The batch wrapper would exceed its scalar bound; send
                        # this invocation's already bounded arguments separately.
                        index, context, regulation = group[0]
                        error = self.runner.sandbox.call("prepare-regulation", index, encode(context), encode(regulation), fuel=PREPARATION_FUEL)
                    else:
                        error = self.runner.sandbox.call("prepare-regulations", packet, fuel=PREPARATION_FUEL)
                    if error:
                        raise SandboxError(("Regulation preparation failed: " + error)[:512])
                    for index in range(len(group)):
                        # Sequential one-use slots retain ordinary execution
                        # fuel/host calls; neither preparation nor siblings spend it.
                        reply = self.runner.sandbox.call("validate-regulation", index, fuel=Limits().fuel)
                        results.append(decision(decode(reply)))
            if time.monotonic() >= deadline:
                raise SandboxError("Late regulation evaluation")
            return results
        except BaseException:
            self.runner.close()
            raise
