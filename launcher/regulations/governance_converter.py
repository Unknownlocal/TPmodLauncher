# SPDX-License-Identifier: MPL-2.0
"""Check category change policies and owned voting references without running code."""

from . import schema


# These native categories do not use the ordinary team-vote calculation.
_SPECIAL_CATEGORIES = frozenset({"calendar", "calendar_schedule", "calendar_tracks",
                                "technical_regulation_era", "formula_team_entry_cap",
                                "formula_team_entry_requirement"})


def automatic_changes(categories):
    """Separate governance settings from the definitions native saves compare."""
    policies = {}
    for key, category in categories.items():
        if "change_control" not in category:
            continue
        policy = category["change_control"]
        schema.fields(policy, ("mode", "base_chance", "announcement_week"))
        chance, week = policy["base_chance"], policy["announcement_week"]
        if policy["mode"] != "automatic":
            raise ValueError("Custom change control requires automatic mode")
        if type(chance) not in (int, float) or not 0 <= chance <= 1:
            raise ValueError("Automatic change chance must be finite and between 0 and 1")
        # Game weeks are stored as zero-based indexes, including season week 0.
        if type(week) is not int or not 0 <= week < 52:
            raise ValueError("Automatic announcement week must be an integer from 0 to 51")
        policies[key] = category.pop("change_control")
    return policies


def voting_handlers(value, source, accessible, index, automatic):
    """Admit one owned calculation per ordinary native or owned custom category."""
    rows = schema.rows(value, 32)
    paths = index.source_paths(source) if rows else frozenset()
    for row in rows:
        schema.fields(row, ("rule", "handler"))
        key = schema.rule(row["rule"], accessible)
        if key in _SPECIAL_CATEGORIES:
            raise ValueError("Voting handler requires an ordinary voting category")
        if key in automatic:
            raise ValueError("Automatic category cannot declare a voting handler")
        schema.handler(row["handler"], paths)
        index.option_keys(key, accessible[key])
    return rows
