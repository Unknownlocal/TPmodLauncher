# SPDX-License-Identifier: MPL-2.0
"""Lazy derived data shared only within one regulation preparation attempt."""

from . import payload, schema


class PreparationIndex:
    """Reuse reference indexes and native minimum envelopes across owners.

    Category keys are session-unique; collisions discard the entire attempt.
    Lazy preparation leaves unreferenced native definitions semantically intact.
    No cache survives a pack change or captures author code execution.
    """

    def __init__(self):
        self._options, self._payloads, self._sources = {}, {}, {}

    def option_keys(self, key, category):
        if key not in self._options:
            self._options[key] = schema.option_keys(category)
        return self._options[key]

    def payloads(self, key, category):
        if key not in self._payloads:
            values = []
            for option in category["options"]:
                schema.parameters(option.get("parameters", {}))
                values.append((option["key"], payload.minimum(key, option)))
            self._payloads[key] = values
        return self._payloads[key]

    def source_paths(self, source):
        # All admitted snapshots stay alive throughout this attempt, so their
        # immutable files tuples can be indexed by identity without ID reuse.
        identity = id(source.files)
        if identity not in self._sources:
            self._sources[identity] = frozenset(path for path, _ in source.files)
        return self._sources[identity]
