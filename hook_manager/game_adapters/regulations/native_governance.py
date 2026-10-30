# SPDX-License-Identifier: MPL-2.0
"""Own shared governance construction, saved restoration and progress retention."""

from functools import update_wrapper
from pathlib import Path
import threading

from ...callables import CallableTarget
from ...manager import HookError, Manager
from .automatic_progress import AutomaticProgress, FIELD


class NativeGovernance(Manager):
    """Keep persistence with the bridge, independent of enabled mod execution.

    A category loader may attach while its pack is active. Its script merging
    shares these wrappers and the same active-operation count. Each native
    governance instance owns its own progress, including inactive categories.
    """

    def __init__(self, report, completed=()):
        super().__init__(lambda record: None)
        # The import observer runs before Python clears the module's loading
        # flag. Its completed-module record permits installation at that point.
        self.completed.update(completed)
        self._lock, self._active = threading.RLock(), 0
        self.categories, self.report = None, report
        module = "managers.formula_regulation_governance"
        try:
            bindings = []
            for method, signature in (("__init__", "(self, state, saved_bootstrap=None)"),
                                      ("load_state", "(self, data)"), ("save_state", "(self)")):
                target = CallableTarget("regulations.governance." + method, module,
                                        ("FormulaRegulationGovernanceManager",), method, signature)
                self.approve(target)
                bindings.append({"target": target.name, "callback": "internal", "phase": "after"})
            self.install("regulations.governance", bindings)
            self.check()
        except BaseException:
            self.stop()
            raise

    def check(self):
        """Require the complete native load/save group before attaching mod data."""
        group = self.groups["regulations.governance"]
        if (self.closed or group["state"] != "active" or any(
                vars(owner).get(attribute) is not wrapper for owner, attribute, _, wrapper, _ in group["changes"])):
            raise HookError("Native governance load/save hooks are unavailable")

    def _wrapper(self, name, binding, target, original, signature):
        # Keep the native result and exception; only completed calls reach the
        # metadata and script work. Cleanup waits for this whole operation.
        group, method = self.groups[name], target.attribute

        def wrapped(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            manager = arguments["self"]
            with self._lock:
                if group["state"] != "active":
                    return original(*args, **kwargs)
                self._active += 1
            try:
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
                                    + "; automatic changes unavailable; native governance retained")
                categories = self.categories
                if categories is not None:
                    try:
                        if (Path(manager.state.data_dir).resolve() == categories.pack
                                and categories.scripts is not None):
                            categories.scripts.merge(manager)
                    except Exception as error:
                        categories._report(error)
                return result
            finally:
                with self._lock:
                    self._active -= 1

        update_wrapper(wrapped, original)
        wrapped._tpm_hook_group = name
        return wrapped
