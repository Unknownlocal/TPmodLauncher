# SPDX-License-Identifier: MPL-2.0
"""Keep enabled mods' scripted changes in the game's loaded governance policy."""

from copy import deepcopy

from ...callables import CallableTarget
from ...manager import HookError


class NativeScripts:
    """Merge prepared script rows using the shared native governance loader.

    These are scheduled JSON choices, not Python files. NativeCategories owns
    the declarations; NativeGovernance owns construction and restoration hooks.
    The game keeps scheduling, notices, completion records and rule adoption.
    """

    def __init__(self, categories, scripts):
        self.categories = categories
        self.scripts = {row["id"]: deepcopy(row) for row in scripts}
        if (not self.scripts or len(scripts) > 4096 or len(self.scripts) != len(scripts)
                or any(row["key"] not in categories.categories for row in scripts)):
            raise HookError("Expected distinct prepared scripts for owned categories")
        normalize = CallableTarget("regulations.script-policy", "managers.formula_regulation_governance",
                                   (), "normalize_policy", "(raw, definitions)")
        resolved = normalize.resolve({})
        if resolved is None:
            raise HookError("Script loading needs the loaded native governance module")
        self.normalize = resolved[2]

    def merge(self, manager):
        """Build a complete checked policy before replacing either native field."""
        definitions = dict(manager.definitions)
        # A save freezes its category list. Fill gaps for newly enabled rules,
        # but report changed saved definitions instead of silently replacing them.
        for key, category in self.categories.categories.items():
            if key in definitions:
                if definitions[key] != category:
                    raise HookError("Saved mod category differs from its prepared definition: " + key)
            else:
                definitions[key] = deepcopy(category)
        rows = list(manager.policy["scripted_changes"])
        existing = {row["id"]: row for row in rows}
        # Native completion/suppression records use these IDs. Keep matching
        # rows once; assigning new IDs would detach them from that saved progress.
        for identity, row in self.scripts.items():
            if identity in existing:
                if existing[identity] != row:
                    raise HookError("Saved mod script differs from its prepared definition: " + identity)
            else:
                rows.append(row)
        # Native validation checks IDs, category/season conflicts and options.
        # Preserve the loaded mode: adding a script never disables native voting.
        policy = self.normalize(dict(manager.policy, scripted_changes=rows), definitions)
        manager.definitions, manager.policy = definitions, policy

    def check(self, state):
        """Confirm saved loading retained the scripts before policy publication."""
        self.categories.governance.check()
        manager = state.formula_regulation_governance
        rows = {row["id"]: row for row in manager.policy["scripted_changes"]}
        if (manager.state is not state or any(rows.get(identity) != row for identity, row in self.scripts.items())
                or any(manager.definitions.get(key) != category for key, category in self.categories.categories.items())):
            raise HookError("The career did not load the prepared mod regulation scripts")
