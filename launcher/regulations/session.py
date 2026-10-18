# SPDX-License-Identifier: MPL-2.0
"""Check cross-owner collisions and aggregate quotas after owner conversion."""


class RegulationSession:
    """Own provisional declarations/reservations for one atomic preparation.

    Owner conversion checks its own references. This stage enforces shared
    namespaces and quotas; failure discards the session rather than publishing a
    subset of otherwise valid owners. It does not adopt or schedule policies.
    """

    def __init__(self, native_keys, script_ids, script_targets):
        self.owners = {}
        self._reserved = set(native_keys)
        self._script_ids, self._script_targets = script_ids, script_targets
        self._categories = self._validators = 0

    def add(self, owner, model):
        """Reserve one converted owner's declarations against all earlier rows."""
        try:
            keys = {row["key"] for row in model.categories}
            if keys & self._reserved:
                raise ValueError("Custom category collides with an existing definition")
            self._reserved.update(keys)
            for row in model.scripted_changes:
                if row["id"] in self._script_ids:
                    raise ValueError("Duplicate scripted change identity")
                target = (row["key"], row["effective_season"])
                if target in self._script_targets:
                    raise ValueError("Duplicate scripted change category/season")
                self._script_ids.add(row["id"])
                self._script_targets.add(target)
            self.owners[owner] = model
            self._categories += len(model.categories)
            self._validators += len(model.validators)
        except ValueError as error:
            raise ValueError(f"Regulation owner {owner}: {error}") from error

    def finish(self):
        """Reject aggregate excess before revision finalization or model creation."""
        if self._categories > 128 or self._validators > 256:
            raise ValueError("Regulation session quota exceeded")
        return self.owners
