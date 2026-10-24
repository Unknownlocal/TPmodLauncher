# SPDX-License-Identifier: MPL-2.0
"""Bind and call regulation handlers through dedicated private runner exports."""

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode, fields
from mod_runtime.runtime import PythonRunner, SOURCE_FUEL, SOURCE_SECONDS


def ordered_bindings(validators):
    """Keep each owner's admitted IDs in the same order as its private slots."""
    if type(validators) is not list or len(validators) > 32:
        raise ValueError("Invalid regulation binding count")
    for row in validators:
        fields(row, ("id", "rule", "scope", "handler"))
    ordered = sorted(validators, key=lambda row: row["id"])
    if len({row["id"] for row in ordered}) != len(ordered):
        raise ValueError("Duplicate regulation validator ID")
    return {row["id"]: (index, {key: row[key] for key in ("rule", "scope", "handler")})
            for index, row in enumerate(ordered)}


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
        """Return the single trusted validation helper for this bound Store."""
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
            bindings = ordered_bindings(validators)
            with runner.sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                for index, declaration in bindings.values():
                    error = runner.sandbox.call("bind-regulation", index, encode({"owner": runner.manifest.id, **declaration}))
                    if error:
                        raise SandboxError(("Regulation binding failed: " + error)[:512])
            return cls(runner, bindings)
        except BaseException:
            if type(runner) is PythonRunner:
                runner.close()
            raise
