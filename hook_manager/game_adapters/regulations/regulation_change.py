# SPDX-License-Identifier: MPL-2.0
"""Keep native rule persistence and prepared weekly decisions together."""

from functools import update_wrapper
from contextlib import contextmanager
import inspect
from pathlib import Path
import threading

from ...callables import CallableTarget
from ...manager import HookError, Manager
from .automatic_progress import AutomaticProgress, FIELD
from .automatic_changes import AutomaticChanges


class RegulationChangeAdapter(Manager):
    """Keep persistence with the bridge, independent of enabled mod execution.

    A category loader may attach while its pack is active. Script merging and
    category-owned weekly hooks share the same active-operation count. Each
    native manager owns its own progress, including inactive categories.
    """

    def __init__(self, report, completed=()):
        super().__init__(lambda record: None)
        # The import observer runs before Python clears the module's loading
        # flag. Its completed-module record permits installation at that point.
        self.completed.update(completed)
        self._lock, self._active = threading.RLock(), 0
        self.categories, self.report = None, report
        self.voting_pack = None
        # Native lookup uses the game's own module and class identifiers.
        module = "managers.formula_regulation_governance"
        try:
            bindings = []
            for method, signature in (("__init__", "(self, state, saved_bootstrap=None)"),
                                      ("load_state", "(self, data)"), ("save_state", "(self)")):
                target = CallableTarget("regulations.changes." + method, module,
                                        ("FormulaRegulationGovernanceManager",), method, signature)
                self.approve(target)
                bindings.append({"target": target.name, "callback": "internal", "phase": "after"})
            self.install("regulations.changes", bindings)
            self.check()
        except BaseException:
            self.stop()
            raise

    def check(self):
        """Require the complete native load/save group before attaching mod data."""
        group = self.groups["regulations.changes"]
        if (self.closed or group["state"] != "active" or any(
                vars(owner).get(attribute) is not wrapper for owner, attribute, _, wrapper, _ in group["changes"])):
            raise HookError("Native regulation change load/save hooks are unavailable")

    def install_automatic(self, policies):
        """Install weekly work for this pack's prepared automatic policies.

        Weekly wrappers belong to the category loader so removing its pack also
        removes its decisions. Career progress keeps its separate save/load hooks.
        """
        self.check()
        categories = self.categories
        if categories is None or categories.automatic is not None:
            raise HookError("Automatic changes need an available category loader")
        helper = AutomaticChanges(categories, policies, self.report)
        categories.completed.update(self.completed)
        bindings = []
        for method, signature in (("process_week", "(self)"), ("_trigger_candidates", "(self, snapshot)")):
            target = CallableTarget("regulations.automatic." + method, "managers.formula_regulation_governance",
                                    ("FormulaRegulationGovernanceManager",), method, signature)
            if target.resolve(categories.completed) is None:
                raise HookError("Automatic changes need the loaded weekly regulation methods")
            categories.approve(target)
            bindings.append({"target": target.name, "callback": "internal", "phase": "after"})
        categories.automatic = helper
        try:
            categories.install("regulations.automatic", bindings)
        except BaseException:
            categories.automatic = None
            raise

    def defer_voting(self, pack):
        """Hold load-time ballot filling until this pack's career is activated."""
        if pack is None:
            self.voting_pack = None
            return
        self.check()
        if type(pack) is not str or not Path(pack).is_absolute():
            raise HookError("Voting protection needs an absolute pack folder")
        target = CallableTarget("regulations.voting.load-check", "managers.formula_regulation_governance",
                                ("FormulaRegulationGovernanceManager",), "_resolve_uncontrolled_reviews", "(self)")
        if target.resolve(self.completed) is None:
            raise HookError("Native load-time voting method is unavailable")
        self.voting_pack = Path(pack).resolve(strict=True)

    @contextmanager
    def _voting_load(self, manager, method):
        applicable = (method == "load_state" and self.voting_pack is not None
                      and getattr(manager.state, "game_mode", "formula") != "oval"
                      and Path(manager.state.data_dir).resolve() == self.voting_pack)
        if not applicable:
            yield
            return
        # Only this loading manager is affected. The class method stays owned
        # by its normal career adapter, including while another save is loaded.
        if str(inspect.signature(manager._resolve_uncontrolled_reviews)) != "()":
            raise HookError("Native load-time voting signature changed")
        missing = object()
        previous = vars(manager).get("_resolve_uncontrolled_reviews", missing)
        def deferred():
            manager._tpm_voting_pending = True
        manager._resolve_uncontrolled_reviews = deferred
        try:
            yield
        finally:
            if vars(manager).get("_resolve_uncontrolled_reviews") is not deferred:
                raise HookError("Native load-time voting interception was replaced")
            if previous is missing:
                del manager._resolve_uncontrolled_reviews
            else:
                manager._resolve_uncontrolled_reviews = previous

    def _wrapper(self, name, binding, target, original, signature, *, group=None):
        # Keep the native result and exception; only completed calls reach the
        # metadata and script work. Cleanup waits for this whole operation.
        # Weekly groups belong to the category loader; persistence uses our group.
        group, method = self.groups[name] if group is None else group, target.attribute

        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            manager = arguments["self"]
            with self._lock:
                if group["state"] != "active":
                    return original(*args, **kwargs)
                self._active += 1
            try:
                categories = self.categories
                if method in ("process_week", "_trigger_candidates"):
                    helper = categories.automatic if categories is not None else None
                    applicable = (helper is not None and getattr(manager.state, "game_mode", "formula") != "oval"
                                  and Path(manager.state.data_dir).resolve() == categories.pack)
                    if method == "_trigger_candidates" and applicable:
                        return helper.candidates(original, manager, arguments["snapshot"])
                    result = original(*args, **kwargs)
                    if applicable:
                        # Native scripts and approved reviews get their slot first.
                        helper.process(manager)
                    return result
                with self._voting_load(manager, method):
                    result = original(*args, **kwargs)
                # Oval careers do not use these automatic Formula policies.
                if getattr(manager.state, "game_mode", "formula") == "oval":
                    return result
                if method == "save_state":
                    progress = getattr(manager, "_tpm_automatic_progress", None)
                    if progress is not None and progress.present:
                        result[FIELD] = progress.snapshot()
                    return result
                saved = arguments.get("data") if method == "load_state" else arguments.get("saved_bootstrap")
                if method == "load_state" or not hasattr(manager, "_tpm_automatic_progress"):
                    progress = AutomaticProgress(saved)
                    manager._tpm_automatic_progress = progress
                    if progress.error:
                        self.report("REGULATIONS COMPATIBILITY FAILED automatic progress: " + progress.error
                                    + "; automatic changes unavailable; native regulation changes retained")
                if categories is not None:
                    try:
                        if Path(manager.state.data_dir).resolve() == categories.pack:
                            if categories.scripts is not None:
                                categories.scripts.merge(manager)
                            else:
                                manager.definitions = categories.merge_definitions(manager.definitions)
                    except Exception as error:
                        categories._report(error)
                return result
            finally:
                with self._lock:
                    self._active -= 1

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
