# SPDX-License-Identifier: MPL-2.0
"""Choose an admitted native-check boolean from the game's adopted rule option."""

from contextlib import contextmanager
import threading

from ...callables import CallableTarget
from ...manager import HookError


class NativeRuleSelection:
    """Read a small option-to-boolean map without calling mods or rereading JSON."""

    _local = threading.local()

    @staticmethod
    def binding(value):
        """Recognise the closed reference format; it cannot name a game callable."""
        return (type(value) is dict and set(value) == {"rule", "parameter"}
                and all(type(value[key]) is str and 1 <= len(value[key]) <= limit
                        for key, limit in (("rule", 256), ("parameter", 128))))

    def __init__(self, state, binding):
        if not self.binding(binding):
            raise HookError("Expected a native check boolean or rule/parameter reference")
        target = CallableTarget("regulations.native-option", "managers.regulation_manager", ("RegulationManager",),
                                "_option_key_for_season", "(self, category_key: 'str', season_no: 'int') -> 'Optional[str]'")
        resolved = target.resolve({})
        self.state, self.manager, self.rule = state, state.regulation_manager, binding["rule"]
        if resolved is None or not isinstance(self.manager, resolved[0]) or self.manager.state is not state:
            raise HookError("Native check selection needs the loaded regulation manager")
        self.choose, self.definitions = resolved[2], self.manager.regulation_defs
        options = self.definitions[self.rule]["options"]
        self.values = {option["key"]: option.get("parameters", {}).get(binding["parameter"]) for option in options}
        if not self.values or any(type(value) is not bool for value in self.values.values()):
            raise HookError("Every referenced native-check option needs a boolean parameter")

    def scoped_season(self):
        """Return the enclosing proposal's season only for this same career."""
        current = getattr(self._local, "selection", None)
        return current[1] if current is not None and current[0] is self.state else None

    def varies_by_season(self):
        """Avoid native date/roster queries when both seasons use the same toggle."""
        row = self.manager.regulations[self.rule]
        upcoming = row.get("upcoming")
        return upcoming is not None and self.values[row["current"]] != self.values[upcoming]

    def enabled(self, season=None):
        """Use the enclosing contract's season, or a native query's inferred season."""
        if self.state.regulation_manager is not self.manager or self.manager.regulation_defs is not self.definitions:
            raise HookError("Native check definitions need new preparation")
        selected = self.scoped_season()
        if selected is not None:
            season = selected
        elif season is None:
            season = self.state.season_no
        return self.values[self.choose(self.manager, self.rule, season)]

    @classmethod
    @contextmanager
    def scope(cls, state, season=None, *, start_week=None):
        """Keep the proposed season local to this thread and restore nested callers.

        Offer preparation has a start week; final signing has a prepared season.
        A helper with no enclosing proposal uses its ordinary native inference.
        """
        if season is None and start_week is not None:
            season = state.season_no + int(start_week >= state.next_season_start())
        if season is None:
            yield
            return
        if type(season) is not int or season < 1:
            raise HookError("Native check selection needs a valid contract season")
        previous = getattr(cls._local, "selection", None)
        cls._local.selection = state, season
        try:
            yield
        finally:
            cls._local.selection = previous
