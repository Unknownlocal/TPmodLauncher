# SPDX-License-Identifier: MPL-2.0
"""Prepare subscribed rule choices once and let the game select between them."""

from copy import deepcopy
import sys

from ...callables import CallableTarget
from ...manager import HookError
from .protocol import regulation_policy_value


class RegulationPolicy:
    """Own one career's prepared rule data and its small choice references.

    The channel supplies admitted rule names, never author code. Read choices
    from the already loaded native manager; do not read regulations.json again.
    Only publication sends the stored data. Each later proposal uses indexes
    into that data and the game's own season-selection method.
    """

    def __init__(self, state, channel):
        self.state, self.channel = state, channel
        self._native, self._custom, self._indexes, self._adopted = {}, {}, {}, {}
        self._saved = {}
        self._published = False
        # The channel already combines every owner's subscriptions. Prepare
        # each rule once, even when many mods subscribe to the same choice.
        rules = channel._rules
        if not rules:
            return  # Boolean switches alone need no rule inspection or selector.
        target = CallableTarget("regulations.option-for-season", "managers.regulation_manager",
            ("RegulationManager",), "_option_key_for_season",
            "(self, category_key: 'str', season_no: 'int') -> 'Optional[str]'")
        resolved = target.resolve({})
        self.manager = state.regulation_manager
        if resolved is None or not isinstance(self.manager, resolved[0]) or self.manager.state is not state:
            raise HookError("Regulation selection needs the loaded native manager")
        self._choose = resolved[2]
        self._definitions, self._season = self.manager.regulation_defs, state.season_no
        if type(self._season) is not int or self._season < 1:
            raise HookError("Regulation selection needs a loaded career season")
        for rule in sorted(rules):
            options = self._definitions[rule]["options"]
            if type(options) is not list or not options:
                raise HookError("Subscribed regulation has no loaded choices: " + rule)
            choices, indexes = [], {}
            custom = rule in channel._custom
            for option in options:
                key = option["key"]
                if key in indexes:
                    # The native _option_data helper uses the first matching
                    # key. Later duplicate definitions are not reachable choices.
                    continue
                indexes[key] = len(indexes)
                if custom:
                    regulation_policy_value("custom", key)
                else:
                    value, _, _ = regulation_policy_value("native", option)
                    choices.append(deepcopy(value))
            self._indexes[rule] = indexes
            if custom:
                # Custom parameters already belong to the launcher's admitted
                # catalogue. Publishing their keys cannot replace those values.
                self._custom[rule] = list(indexes)
            else:
                self._native[rule] = choices
            self._prepare_saved(rule)
            self._adopted[rule] = self._adoption(rule)

    def _prepare_saved(self, rule):
        """Include a loaded calendar action without replacing the base options."""
        row = self.manager.regulations[rule]
        raw = row.get("upcoming_option")
        if raw is None:
            return
        native = sys.modules["managers.regulation_manager"]
        if rule != getattr(native, "CALENDAR_KEY", None) or rule in self._custom:
            raise HookError("Saved regulation replacement needs its native data adapter: " + rule)
        value, _, _ = regulation_policy_value("native", raw)
        if value["key"] != row.get("upcoming"):
            raise HookError("Saved calendar action does not match its upcoming choice")
        choices = self._native[rule]
        # The catalogue already accepts distinct payloads sharing a key. Reuse
        # an identical record, but never replace the base option's current index.
        if value in choices:
            index = choices.index(value)
        else:
            index = len(choices)
            choices.append(deepcopy(value))
        # Retain the native copy only to recognise it later. Published values
        # are independent, and native staging never edits this copy in place.
        self._saved[rule] = raw, index

    def _adoption(self, rule):
        """Read only the small current/upcoming identity, not complete rule data."""
        row = self.manager.regulations[rule]
        current, upcoming = row["current"], row.get("upcoming")
        indexes = self._indexes[rule]
        upcoming_index = indexes.get(upcoming)
        raw = row.get("upcoming_option")
        if raw is not None:
            saved = self._saved.get(rule)
            # Native staging replaces the whole copied action, and rollover
            # removes it. A new object needs publication before it can be used;
            # ordinary checks do not encode or copy that payload again.
            if saved is None or raw is not saved[0]:
                raise HookError("Saved regulation replacement needs a new preparation: " + rule)
            upcoming_index = saved[1]
            if self._native[rule][upcoming_index]["key"] != upcoming:
                raise HookError("Saved calendar action does not match its upcoming choice")
            if current == upcoming and upcoming_index != indexes.get(current):
                # The native selector returns only a key; it cannot distinguish
                # different current/upcoming payloads with that very same key.
                raise HookError("Saved calendar action has an ambiguous current choice")
        if current not in indexes or upcoming is not None and upcoming_index is None:
            raise HookError("Native regulation choice is missing from prepared data: " + rule)
        season = row.get("upcoming_effective_season", self.state.season_no + 1) if upcoming is not None else None
        if season is not None and (type(season) is not int or season < 1):
            raise HookError("Invalid native upcoming regulation season")
        return current, upcoming, season, upcoming_index

    def _current(self):
        # Boolean-only policies have no native choice data to invalidate.
        if (self.channel.closed or self._indexes and (
                self.state.regulation_manager is not self.manager or self.manager.state is not self.state
                or self.manager.regulation_defs is not self._definitions
                or type(self.state.season_no) is not int or self.state.season_no < 1)):
            raise HookError("Prepared native regulation policy is no longer current")

    def identity(self):
        """Read the small live selection inputs, without copying stored option data.

        All reachable options were acknowledged at setup. Ordinary adoption and
        rollover change only which of those options the game selects. Guards
        compare these inputs before and after validation or a staffing batch.
        """
        self._current()
        if not self._indexes:
            return ()
        return self.state.season_no, tuple((rule, self._adoption(rule)) for rule in self._indexes)

    def publish(self):
        """Send the prepared choices once; successful acknowledgement enables selection.

        The launcher must service the connection while this sends the data.
        Waiting for a blocking bridge reply on that same controller would
        prevent it from acknowledging the data.
        This method alone neither installs guards nor acknowledges their coverage.
        """
        self._current()
        if self._published:
            raise HookError("Native regulation policy is already published")
        expected = (self._season, tuple(self._adopted.items())) if self._indexes else None
        if self._indexes and self.identity() != expected:
            raise HookError("Native regulation choices changed before publication")
        self.channel.publish(native=self._native, custom=self._custom)
        # Socket waits release Python's lock. An acknowledgement for sent data
        # cannot make a career/rule change during that wait safe to use.
        self._current()
        if self._indexes and self.identity() != expected:
            raise HookError("Native regulation choices changed during publication")
        self._published = True

    def select(self, rule, season):
        """Return references into published data for one proposal's season."""
        self._current()
        if not self._published or rule not in self._indexes or type(season) is not int or season < 1:
            raise HookError("Native regulation selection is unavailable")
        # Adoption metadata is live; the option catalogue it references stays
        # resident. The native selector still decides how each rule uses seasons.
        current, upcoming, upcoming_season, upcoming_index = self._adoption(rule)
        key = self._choose(self.manager, rule, season)
        if key == current:
            selected = "current"
        elif upcoming is not None and key == upcoming:
            selected = "upcoming"
        else:
            raise HookError("Native selection is outside the prepared current/upcoming choices")
        # Native saves do not record the historical adoption date of the current
        # choice. This season tags when we observed it in force, not when it was
        # first adopted. Upcoming dates come from the native record/default.
        indexes = self._indexes[rule]
        return {"selected": selected, "current": [indexes[current], self.state.season_no],
                "upcoming": [upcoming_index, upcoming_season] if upcoming is not None else None}
