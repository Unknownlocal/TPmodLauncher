# SPDX-License-Identifier: MPL-2.0
"""Hold native staff changes until the complete staffing operation can publish.

Native recovery can generate people and sign earlier contracts before a later
contract fails validation. Keep those effects private, then apply the prepared
changes to the game's original people and containers only after all checks pass.
This file handles native ownership and staleness; regulation permission is separate.
"""

from copy import copy

from ...manager import HookError
from ..native_values import native_value
from ..personnel.market_graph import MarketGraph


class StaffingPublication:
    def __init__(self, state):
        self.state, self.manager = state, state.staff_manager
        # Compare live values and container identities before publishing writes.
        # The existing market helper preserves shared staff dicts/lists;
        # there is no need to copy the whole career or introduce a second copier.
        self.graph = MarketGraph()
        # Both original and private person IDs resolve to the same saved pair.
        self.people = {}
        self.before = self._inputs()
        self.used = False

    def _team(self, name, team):
        # Native recovery skips non-record team rows and tolerates malformed HQ
        # data. Observe those shapes without making this check stricter than native.
        if not isinstance(team, dict):
            return name, native_value(team)
        headquarters = team.get("headquarters")
        plant = headquarters.get("engine_plant") if isinstance(headquarters, dict) else headquarters
        return name, id(team), native_value(team.get("active")), native_value(team.get("retired")), native_value(plant)

    def _inputs(self):
        """Remember native read inputs without copying roster people or old seasons."""
        state, manager = self.state, self.manager
        return (id(state.staff_manager), id(manager.state), state.game_mode, state.season_no, state.week_index,
                state.current_season_start_week(), state.next_season_start(), id(manager.staff),
                tuple((id(person), native_value(vars(person))) for person in manager.staff),
                id(manager.staff_season_history), tuple((name, id(rows)) for name, rows in manager.staff_season_history.items()),
                id(state.teams), tuple(self._team(name, team) for name, team in state.teams.items()),
                id(manager._name_pool), native_value(vars(manager._name_pool)))

    def person(self, original):
        """Return the same original/private pair when given either person identity."""
        pair = self.people.get(id(original))
        if pair is None:
            private = copy(original)
            vars(private).clear()
            vars(private).update(self.graph.copy(vars(original)))
            initial = {name: native_value(value) for name, value in vars(private).items()}
            pair = original, private, initial
            self.people[id(original)] = self.people[id(private)] = pair
        return pair[0], pair[1]

    def publish(self, generated, history):
        """Apply prepared native writes once, after proving their inputs still match."""
        try:
            current = self._inputs()
        except (AttributeError, KeyError, TypeError) as error:
            raise HookError("Staffing inputs changed during preparation") from error
        if self.used or self.before != current:
            raise HookError("Staffing preparation is stale or already published")
        # Every pair has two index entries. Visit each original person once and
        # publish changed fields only; unchanged borrowed fields keep their owners.
        people = {id(pair[0]): pair for pair in self.people.values()}
        changes, removals = {}, []
        for original, private, initial in people.values():
            for name in initial.keys() | vars(private).keys():
                if name not in vars(private):
                    removals.append((original, name))
                elif name not in initial or native_value(vars(private)[name]) != initial[name]:
                    changes[id(original), name] = vars(private)[name]
        # Existing historical rows were read-only throughout preparation. Only
        # new history keys join the selected-person changes in this publication.
        changes.update((("history", name), rows) for name, rows in history.items())
        prepared = self.graph.publish(changes)
        for (owner, name), value in prepared.items():
            if owner == "history":
                self.manager.staff_season_history[name] = value
            else:
                setattr(people[owner][0], name, value)
        for person, name in removals:
            delattr(person, name)
        # Generated objects were retained unsigned, so their validated refs stay
        # valid when they become live roster members, including unused native seeds.
        self.manager.staff.extend(generated)
        self.used = True
