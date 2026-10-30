# SPDX-License-Identifier: MPL-2.0
"""Choose seasonal automatic changes while native code owns their adoption."""

from copy import copy, deepcopy
import hashlib
import json
from random import Random

from ...callables import CallableTarget
from ...manager import HookError


class AutomaticChanges:
    """Process trusted, prepared policies without calling a mod interpreter.

    The category adapter owns this helper and its weekly hook group. The change
    adapter supplies the wrappers; progress stays on each career's native manager.
    """

    def __init__(self, categories, policies, report):
        if (type(policies) is not dict or not policies or not policies.keys() <= categories.categories.keys()
                or any(type(row) is not dict or set(row) != {"mode", "base_chance", "announcement_week"}
                       or row["mode"] != "automatic" or type(row["base_chance"]) not in (int, float)
                       or not 0 <= row["base_chance"] <= 1 or type(row["announcement_week"]) is not int
                       or not 0 <= row["announcement_week"] < 52 for row in policies.values())):
            raise HookError("Expected prepared automatic policies for owned categories")
        target = CallableTarget("regulations.automatic.neighbours", "managers.formula_regulation_governance",
                                (), "option_neighbours", "(spec, current_key)")
        resolved = target.resolve(categories.regulation_changes.completed)
        if resolved is None:
            raise HookError("Automatic changes need the loaded native option selector")
        self.neighbours = resolved[2]
        stage = CallableTarget("regulations.automatic.stage", "managers.formula_regulation_governance",
                               ("FormulaRegulationGovernanceManager",), "stage_changes",
                               "(self, changes, *, source=None, decision_id=None, effective_season=None)")
        if stage.resolve(categories.regulation_changes.completed) is None:
            raise HookError("Automatic changes need the loaded native staging method")
        self.categories, self.policies, self.report = categories, deepcopy(policies), report

    def candidates(self, original, manager, snapshot):
        """Exclude automatic categories before native voting quotas are filled."""
        view = copy(manager)
        view.definitions = {key: value for key, value in manager.definitions.items() if key not in self.policies}
        return original(view, snapshot)

    def process(self, manager):
        """Check due categories independently; a failed rule cannot stop the week."""
        for key, policy in self.policies.items():
            try:
                self._decide(manager, key, policy)
            except Exception as error:
                self.report("REGULATIONS COMPATIBILITY FAILED automatic change " + key + ": " + str(error)
                            + "; native regulation changes retained")

    def _staged_option(self, manager, key, season):
        """Recover only a complete native stage bearing this decision's identity."""
        regulation = manager.state.regulation_manager
        upcoming = regulation.regulations[key].get("upcoming")
        pending = regulation.pending_rules.get(key)
        if pending != upcoming:
            raise HookError("Native pending and upcoming choices disagree")
        source = manager.staged_sources.get(key, {})
        if source.get("id") != f"automatic:{key}:{season}":
            return None
        if (source.get("source") != "automatic" or source.get("effective_season") != season + 1
                or upcoming not in {row["key"] for row in manager.definitions[key]["options"]}):
            raise HookError("Native automatic staging is incomplete or inconsistent")
        return upcoming

    def _decide(self, manager, key, policy):
        """Persist no-change outcomes too, so reloads cannot draw again."""
        progress = manager._tpm_automatic_progress
        if progress.error:
            raise HookError("Automatic progress is unavailable: " + progress.error)
        season = manager.state.season_no
        previous = progress.data["categories"].get(key, {"checked_season": 0, "pending_announcements": []})
        if previous["checked_season"] > season:
            raise HookError("Automatic progress is ahead of the career season")
        if previous["checked_season"] == season:
            return
        record = {"checked_season": season, "pending_announcements": list(previous["pending_announcements"])}
        option = self._staged_option(manager, key, season)
        if option is not None:
            record["pending_announcements"].append({"decision_season": season, "option": option})
            progress.store_record(key, record)
            return
        if manager.state.week_index < policy["announcement_week"]:
            return
        # A scheduled script or any already approved choice keeps its slot.
        scripted = any(row["key"] == key and row["effective_season"] == season + 1
                       and row["id"] not in manager.suppressed_script_ids
                       for row in manager.policy["scripted_changes"])
        regulation = manager.state.regulation_manager.regulations[key]
        if not scripted and regulation.get("upcoming") is None:
            if manager.definitions.get(key) != self.categories.categories[key]:
                raise HookError("Saved automatic category differs from its prepared definition")
            choices = dict.fromkeys(self.neighbours(manager.definitions[key], regulation["current"]))
            choices = [choice for choice in choices if choice != regulation["current"]]
            # Each rule has a separate seed. Neither rule ordering nor unrelated
            # draws change its result, and the game's global random state is untouched.
            seed = json.dumps([manager.seed, key, season], ensure_ascii=True, separators=(",", ":"))
            random = Random(hashlib.sha256(seed.encode("ascii")).digest())
            if choices and random.random() < policy["base_chance"]:
                option = random.choice(choices)
        if option is None:
            progress.store_record(key, record)
            return
        record["pending_announcements"].append({"decision_season": season, "option": option})
        # Reserve notice capacity before changing native state. Keep progress if
        # staging completed even when the native call subsequently raises.
        progress.check_record(key, record)
        try:
            manager.stage_changes({key: option}, source="automatic", decision_id=f"automatic:{key}:{season}",
                                  effective_season=season + 1)
        finally:
            staged = self._staged_option(manager, key, season)
            if staged == option:
                progress.store_record(key, record)
            elif staged is not None:
                raise HookError("Native staging selected a different automatic option")
        if staged is None:
            raise HookError("Native staging did not record the automatic decision")
