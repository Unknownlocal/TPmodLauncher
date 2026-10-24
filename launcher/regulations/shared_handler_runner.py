# SPDX-License-Identifier: MPL-2.0
"""Bind distinct regulation owners inside one trusted shared Python runner."""

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode, fields
from mod_runtime.source_transfer import SOURCE_FUEL, SOURCE_SECONDS
from mod_runtime.shared_runtime import SharedPythonRunner


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


class SharedHandlerRunner:
    """Keep original owner/validator IDs while the group owns execution and closure."""

    def __init__(self, runner, validators):
        self.runner, self.validators = runner, validators
        self._validation = None

    def validation(self):
        """Return the one trusted coordinator for this physical resident group."""
        if self._validation is None:
            from .validation.group import ValidationGroup
            self._validation = ValidationGroup(self)
        return self._validation

    @classmethod
    def bind(cls, runner, validators):
        """Admit all tables before policy setup freezes the group's bindings.

        Source activation is checked again inside the guest. No function paths
        reach native code: these declarations resolve only transferred sources.
        """
        try:
            if type(runner) is not SharedPythonRunner or runner.closed:
                raise SandboxError("Expected an admitted shared Python runner")
            if getattr(runner, "_regulation_handlers", None) is not None:
                raise ValueError("Shared regulation handlers must bind once per runner")
            if type(validators) is not dict or not validators or not validators.keys() <= runner.members.keys():
                raise ValueError("Expected admitted shared regulation owners")
            tables = {}
            for owner in sorted(validators):
                manifest = runner.members[owner].manifest
                if manifest.api != "0.4.0" or "regulations" not in manifest.capabilities:
                    raise SandboxError("Expected an admitted regulation source owner")
                tables[owner] = ordered_bindings(validators[owner])
                if not tables[owner]:
                    raise ValueError("Shared regulation owners need at least one validator")
            # Each owner retains its source-import allowance. Binding a larger
            # pack must not quietly reduce every mod's admitted startup budget.
            for owner, table in tables.items():
                with runner.sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                    for index, declaration in table.values():
                        error = runner.sandbox.call("bind-source-regulation", index,
                                                    encode({"owner": owner, **declaration}))
                        if error:
                            raise SandboxError((f"Regulation binding failed for {owner}: " + error)[:512])
            binding = cls(runner, tables)
            # One physical Store cannot host independent catalogs/action IDs
            # through separate binding wrappers, even for disjoint owner sets.
            runner._regulation_handlers = binding
            return binding
        except BaseException:
            if type(runner) is SharedPythonRunner:
                runner.close()
            raise
