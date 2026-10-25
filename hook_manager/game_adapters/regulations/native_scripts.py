# SPDX-License-Identifier: MPL-2.0
"""Keep enabled mods' scripted changes in the game's loaded governance policy."""

from copy import deepcopy
from functools import update_wrapper
from pathlib import Path

from ...callables import CallableTarget
from ...manager import HookError, Manager


class NativeScripts(Manager):
    """Add prepared scripts after native construction and saved-policy restoration.

    These scripts are JSON rows choosing rule options for particular seasons,
    not Python files. The game runs their schedule and records their progress.
    NativeCategories owns this helper and its lifetime. Both loaders share the
    parent's active-load count, so cleanup cannot split an unfinished load.
    Scheduling, notifications, saved progress and applying changes stay native.
    """

    def __init__(self, categories, scripts):
        super().__init__(lambda record: None)
        self.categories = categories
        # Report either loading failure through the pack's existing status path.
        self.errors = categories.errors
        self.scripts = {row["id"]: deepcopy(row) for row in scripts}
        if (not self.scripts or len(scripts) > 4096 or len(self.scripts) != len(scripts)
                or any(row["key"] not in categories.categories for row in scripts)):
            raise HookError("Expected distinct prepared scripts for owned categories")
        try:
            module = "managers.formula_regulation_governance"
            normalize = CallableTarget("regulations.script-policy", module, (), "normalize_policy", "(raw, definitions)")
            resolved = normalize.resolve({})
            if resolved is None:
                raise HookError("Script loading needs the loaded native governance module")
            self.normalize = resolved[2]
            for label, method, signature in (("construct", "__init__", "(self, state, saved_bootstrap=None)"),
                                             ("restore", "load_state", "(self, data)")):
                target = CallableTarget("regulations.scripts." + label, module,
                                        ("FormulaRegulationGovernanceManager",), method, signature)
                if target.resolve({}) is None:
                    raise HookError("Script loading needs the loaded native governance manager")
                self.approve(target)
                self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "after"}])
        except BaseException:
            self.stop()
            raise

    def _merge(self, manager):
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
        if (self.closed or any(group["state"] != "active" or any(
                vars(owner).get(attribute) is not wrapper for owner, attribute, _, wrapper, _ in group["changes"])
                for group in self.groups.values())):
            raise HookError("Mod regulation script hooks are no longer owned")
        manager = state.formula_regulation_governance
        rows = {row["id"]: row for row in manager.policy["scripted_changes"]}
        if (manager.state is not state or any(rows.get(identity) != row for identity, row in self.scripts.items())
                or any(manager.definitions.get(key) != category for key, category in self.categories.categories.items())):
            raise HookError("The career did not load the prepared mod regulation scripts")

    def _wrapper(self, name, binding, target, original, signature):
        """Extend a completed native load without replaying it or changing its result."""
        group, categories = self.groups[name], self.categories

        def wrapped(*args, **kwargs):
            manager = signature.bind(*args, **kwargs).arguments["self"]
            with categories._lock:
                # Cached stopped wrappers still serve native callers; only
                # admitted mod-loading work participates in shared cleanup.
                if group["state"] != "active":
                    return original(*args, **kwargs)
                categories._active += 1
            try:
                result = original(*args, **kwargs)
                try:
                    state = manager.state
                    if (getattr(state, "game_mode", "formula") != "oval"
                            and Path(state.data_dir).resolve() == categories.pack):
                        self._merge(manager)
                except Exception as error:
                    # Keep the completed native load, but withhold mod activation.
                    # Native exceptions above propagate unchanged and run once.
                    self._report(error)
                return result
            finally:
                with categories._lock:
                    categories._active -= 1

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
