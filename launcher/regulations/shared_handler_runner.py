# SPDX-License-Identifier: MPL-2.0
"""Bind distinct regulation owners inside one trusted shared Python runner."""

from launcher.sandbox import SandboxError
from mod_runtime._regulations.policy import encode, fields
from mod_runtime.source_transfer import SOURCE_FUEL, SOURCE_SECONDS
from mod_runtime.shared_runtime import SharedPythonRunner
from . import schema


def ordered_bindings(validators):
    """Keep each owner's admitted IDs in the same order as its private slots."""
    if type(validators) is not list or len(validators) > 32:
        raise ValueError("Invalid regulation binding count")
    for row in validators:
        schema.fields(row, ("id", "rule", "scope", "handler"), ("inputs",))
        schema.validator_inputs(row.get("inputs", []), row["scope"])
    ordered = sorted(validators, key=lambda row: row["id"])
    if len({row["id"] for row in ordered}) != len(ordered):
        raise ValueError("Duplicate regulation validator ID")
    return {row["id"]: (index, {key: row[key] for key in ("rule", "scope", "handler", "inputs") if key in row})
            for index, row in enumerate(ordered)}


class SharedHandlerRunner:
    """Keep original owner/validator IDs while the group owns execution and closure."""

    def __init__(self, runner, validators, voting):
        self.runner, self.validators, self.voting = runner, validators, voting
        self._claimed = False
        self._vote_action = 0

    @classmethod
    def bind(cls, runner, validators, *, voting=None):
        """Admit all tables before policy setup freezes the group's bindings.

        Source activation is checked again inside the guest. No function paths
        reach native code: these declarations resolve only transferred sources.
        """
        try:
            if type(runner) is not SharedPythonRunner or runner.closed:
                raise SandboxError("Expected an admitted shared Python runner")
            if getattr(runner, "_regulation_handlers", None) is not None:
                raise ValueError("Shared regulation handlers must bind once per runner")
            voting = {} if voting is None else voting
            if (type(validators) is not dict or type(voting) is not dict or not (validators or voting)
                    or not (validators.keys() | voting.keys()) <= runner.manifests.keys()):
                raise ValueError("Expected admitted shared regulation owners")
            tables, votes, claimed = {}, {}, set()
            for owner in sorted(validators.keys() | voting.keys()):
                manifest = runner.manifests[owner]
                if manifest.api != "0.4.0" or "regulations" not in manifest.capabilities:
                    raise SandboxError("Expected an admitted regulation source owner")
                if owner in validators:
                    table = ordered_bindings(validators[owner])
                    if table:
                        tables[owner] = table
                rows = voting.get(owner, [])
                if type(rows) is not list or len(rows) > 32:
                    raise ValueError("Invalid voting binding count")
                table = {}
                for row in sorted(rows, key=lambda row: row["rule"]):
                    fields(row, ("rule", "handler"))
                    if row["rule"] in claimed:
                        raise ValueError("Conflicting voting category")
                    claimed.add(row["rule"])
                    table[row["rule"]] = len(table), {"kind": "voting", **row}
                if table:
                    votes[owner] = table
                if owner not in tables and owner not in votes:
                    raise ValueError("Shared regulation owners need at least one handler")
            # Each owner retains its source-import allowance. Binding a larger
            # pack must not quietly reduce every mod's admitted startup budget.
            for owner in sorted(tables.keys() | votes.keys()):
                with runner.sandbox.phase(fuel=SOURCE_FUEL, seconds=SOURCE_SECONDS):
                    for index, declaration in (*tables.get(owner, {}).values(), *votes.get(owner, {}).values()):
                        # Input demand belongs to native preparation; guest
                        # functions keep their existing binding format.
                        guest = {key: value for key, value in declaration.items() if key != "inputs"}
                        error = runner.sandbox.call("bind-source-regulation", index,
                                                    encode({"owner": owner, **guest}))
                        if error:
                            raise SandboxError((f"Regulation binding failed for {owner}: " + error)[:512])
            binding = cls(runner, tables, votes)
            # One physical Store cannot host independent catalogs/action IDs
            # through separate binding wrappers, even for disjoint owner sets.
            runner._regulation_handlers = binding
            return binding
        except BaseException:
            if type(runner) is SharedPythonRunner:
                runner.close()
            raise
