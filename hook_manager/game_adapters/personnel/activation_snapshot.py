# SPDX-License-Identifier: MPL-2.0
"""Detect changes to activation agreements while regulation checks are running."""

from copy import copy

from ...manager import HookError
from ..native_values import native_value


class ActivationSnapshot:
    """Remember proposal inputs before preparation; grant no execution authority."""

    def __init__(self, planner, evaluation_season=None):
        self.planner, self.evaluation_season = planner, evaluation_season
        self.before = self._inputs()

    def _inputs(self):
        """Stamp activation selection and rule input, without copying the career."""
        planner = self.planner
        state = planner.state
        # Keep both calendars: the live clock is rule input, while a rollover can
        # prepare agreements for an explicitly supplied future season.
        calendar = copy(state)
        calendar.season_no = state.season_no if self.evaluation_season is None else self.evaluation_season
        first = planner.methods["state"]["current_season_start_week"][0]
        following = planner.methods["state"]["next_season_start"][0]
        people = []
        for kind in planner.kinds:
            manager = getattr(state, kind + "_manager")
            roster = state.drivers if kind == "driver" else manager.staff
            members = []
            for person in roster:
                future = getattr(person, "future_contract", None)
                employer = future.get("team") if isinstance(future, dict) else None
                # Staff may belong to a supplier. Observe the native lookup's
                # result, so replacing an employer invalidates its earlier ref.
                supplier = manager._supplier_by_id(employer) if kind == "staff" and type(employer) is str else None
                fields = tuple(native_value(getattr(person, name, None)) for name in
                               ("name", "nationality", "age", "role", "team", "contract", "future_contract"))
                members.append((id(person), fields, id(supplier)))
            people.append((kind, id(manager), id(manager.state), id(roster), tuple(members)))
        # Rules receive team identities, not a copy of every team's HQ/history.
        # Colours and callback effects are still calculated by live native code.
        return (state.game_mode, state.season_no, state.week_index, first(state), following(state),
                first(calendar), following(calendar), tuple(people), id(state.teams),
                tuple((name, id(owner)) for name, owner in state.teams.items()))

    def check(self):
        """Reject stale preparation without undoing external game changes."""
        try:
            current = self._inputs()
        except (AttributeError, KeyError, TypeError, RuntimeError) as error:
            raise HookError("Activation inputs changed during validation") from error
        if current != self.before:
            raise HookError("Activation inputs changed during validation")
