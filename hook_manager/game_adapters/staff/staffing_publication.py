# SPDX-License-Identifier: MPL-2.0
"""Hold native staff changes until the complete staffing operation can publish.

Native recovery can generate people and sign earlier contracts before a later
contract fails validation. Keep those effects private, then apply the prepared
changes to the game's original people and containers only after all checks pass.
This file handles native ownership and staleness; regulation permission is separate.
"""

from copy import copy

from ...manager import HookError
from ..personnel.market_graph import MarketGraph


class StaffingPublication:
    def __init__(self, state):
        self.state, self.manager = state, state.staff_manager
        # The existing market helper also preserves shared staff dicts/lists;
        # there is no need to copy the whole career or introduce a second copier.
        self.graph = MarketGraph()
        # Both original and private person IDs resolve to the same saved pair.
        self.people = {}
        self.before = self._inputs()
        self.used = False

    @staticmethod
    def _value(value, seen=None):
        """Compare native values and container identities without JSON conversion."""
        seen = set() if seen is None else seen
        kind = type(value)
        if isinstance(value, (dict, list)):
            # Native helpers accept dict/list subclasses too. Identity alone
            # would miss changes inside them; seen also stops cycles/shared paths.
            if id(value) in seen:
                return kind, id(value)
            seen.add(id(value))
            children = (tuple((key, StaffingPublication._value(item, seen)) for key, item in value.items())
                        if isinstance(value, dict) else tuple(StaffingPublication._value(item, seen) for item in value))
            return kind, id(value), children
        return (kind, value) if kind in (str, int, float, bool, type(None)) else (kind, id(value))

    def _team(self, name, team):
        # Native recovery skips non-record team rows and tolerates malformed HQ
        # data. Observe those shapes without making this check stricter than native.
        if not isinstance(team, dict):
            return name, self._value(team)
        headquarters = team.get("headquarters")
        plant = headquarters.get("engine_plant") if isinstance(headquarters, dict) else headquarters
        return name, id(team), self._value(team.get("active")), self._value(team.get("retired")), self._value(plant)

    def _inputs(self):
        """Remember native read inputs without copying roster people or old seasons."""
        state, manager = self.state, self.manager
        return (id(state.staff_manager), id(manager.state), state.game_mode, state.season_no, state.week_index,
                state.current_season_start_week(), state.next_season_start(), id(manager.staff),
                tuple((id(person), self._value(vars(person))) for person in manager.staff),
                id(manager.staff_season_history), tuple((name, id(rows)) for name, rows in manager.staff_season_history.items()),
                id(state.teams), tuple(self._team(name, team) for name, team in state.teams.items()),
                id(manager._name_pool), self._value(vars(manager._name_pool)))

    def person(self, original):
        """Return the same original/private pair when given either person identity."""
        pair = self.people.get(id(original))
        if pair is None:
            private = copy(original)
            vars(private).clear()
            vars(private).update(self.graph.copy(vars(original)))
            initial = {name: self._value(value) for name, value in vars(private).items()}
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
                elif name not in initial or self._value(vars(private)[name]) != initial[name]:
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
