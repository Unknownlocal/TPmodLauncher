# SPDX-License-Identifier: MPL-2.0
"""Read native future-chassis rule effects without normalising the live career."""

from types import MethodType, SimpleNamespace

from ...callables import method_group
from ...manager import HookError, data_copy

_RULE = "technical_regulation_era"
_INPUTS = ("current", "upcoming", "current_severity", "upcoming_severity", "announcement_sent")
_OUTPUTS = ("key", "severity", "retention", "concept_strength", "concept_attribute_multipliers", "upcoming")
# These native helpers form the announcement-aware read path. Their signatures,
# including annotations, are checked once; no game implementation is copied here.
_METHODS = {
    "_default_regulation_state": "(self) -> 'Dict[str, Dict[str, Optional[str]]]'",
    "_normalize_regulation_state": "(self) -> 'None'",
    "_sanitize_technical_option_key": "(self, raw_key: 'Optional[str]') -> 'str'",
    "_sanitize_technical_severity": "(raw_key: 'Optional[str]') -> 'str'",
    "_technical_state": "(self) -> 'Dict[str, Any]'",
    "_technical_spec": "(self) -> 'Dict[str, Any]'",
    "_technical_severity_data": "(self, severity_key: 'Optional[str]') -> 'Dict[str, Any]'",
    "_option_data": "(self, category_key: 'str', option_key: 'Optional[str]') -> 'Dict[str, Any]'",
    "_build_technical_profile": "(self, era_key: 'Optional[str]', severity_key: 'Optional[str]', *, upcoming: 'bool' = False) -> 'Dict[str, Any]'",
    "current_technical_regulation_profile": "(self) -> 'Dict[str, Any]'",
    "announced_future_technical_regulation_profile": "(self) -> 'Dict[str, Any]'",
}


class TechnicalProfile:
    """Read the technical rules used for a proposed future chassis."""

    def __init__(self, state):
        self.state, self.manager = state, state.regulation_manager
        self.definitions = self.manager.regulation_defs
        self.spec = self.definitions.get(_RULE)
        self.methods = method_group(self.manager, _METHODS)

    def identity(self):
        """Read choices by value because native normalisation replaces the row."""
        if (self.state.regulation_manager is not self.manager or self.manager.state is not self.state
                or self.manager.regulation_defs is not self.definitions
                or self.definitions.get(_RULE) is not self.spec):
            raise HookError("Technical profile belongs to another native policy")
        row = self.manager.regulations.get(_RULE) or {}
        values = tuple(row.get(name) for name in _INPUTS)
        if any(value is not None and type(value) not in (str, bool, int, float) for value in values):
            raise HookError("Technical profile needs plain native choice inputs")
        # Include types: Python otherwise considers True and 1 equal.
        return tuple((type(value), value) for value in values)

    def prepare(self):
        """Run native read helpers on only the relevant copied choice record.

        Native reads replace/normalise regulation rows. A temporary owner keeps
        those writes private; its category definition is borrowed read-only.
        """
        self.identity()
        row = self.manager.regulations.get(_RULE) or {}
        scratch = SimpleNamespace(
            regulation_defs={_RULE: self.spec} if self.spec is not None else {},
            regulations={_RULE: data_copy({name: row[name] for name in _INPUTS if name in row})},
        )
        for name, (function, static) in self.methods.items():
            setattr(scratch, name, function if static else MethodType(function, scratch))
        profile = scratch.announced_future_technical_regulation_profile()
        # Native concept maps have nested aliases into definitions. Detach the
        # operational fields once; presentation strings need not enter Wasm.
        return data_copy({name: profile[name] for name in _OUTPUTS})
