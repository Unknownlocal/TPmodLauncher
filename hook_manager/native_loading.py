# SPDX-License-Identifier: MPL-2.0
"""Observe save entry and existing preload callbacks without frame polling."""

from functools import update_wrapper
import threading
import weakref

if __package__:
    from .manager import Manager
    from .callables import CallableTarget
    from .diagnostics import build_log_record


class NativeLoading(Manager):
    """Keep loading context from initial reads until navigation or cancellation."""

    TARGETS = (
        ("continue", "ui.screens.hub_screen", "HubScreen", "_start_continue_loading", "(self)"),
        ("new", "ui.screens.hub_screen", "HubScreen", "_start_new_season_loading", "(self)"),
        ("selected", "ui.screens.load_game_screen", "LoadGameScreen", "_load_selected", "(self)"),
        ("team", "ui.screens.team_select_screen", "TeamSelectScreen", "_select_current", "(self)"),
        ("load", "game_state", "GameState", "load_season", "(self)"),
        ("construct", "game_state", "GameState", "__init__", "(self, data_dir, save_path=None)"),
        ("job", "ui.session_preload", "SessionPreloader", "__init__", "(self, manager, state, fonts=None, status=None)"),
        ("progress", "ui.session_preload", "SessionPreloader", "_report", "(self, label)"),
        ("cancel", "ui.session_preload", "SessionPreloader", "cancel", "(self)"),
    )

    def __init__(self, readiness, activity):
        super().__init__(lambda record: None)
        self.readiness, self.activity = readiness, activity
        self._jobs = weakref.WeakKeyDictionary()
        # Late workers retain their original attempt without retaining a career.
        self._states = weakref.WeakKeyDictionary()
        for mode, module, owner, method, signature in self.TARGETS:
            target = CallableTarget("loading." + mode, module, (owner,), method, signature)
            self.approve(target)
            try:
                self.install(target.name, [{"target": target.name, "callback": "internal", "phase": "after"}])
            except Exception:
                pass  # Retain compatibility diagnostics; native methods remain usable.

    def store_error(self, error):
        """Retain compatibility history and queue its full warning without source lookup."""
        self.errors.append(str(error)[:4096])
        del self.errors[:-16]
        self.error_record = build_log_record("WARNING", "Loading observation unavailable: " + str(error), component="activity")
        self.activity.submit_log({**self.error_record, "event": "diagnostic"}, error=error)

    def _observe_safely(self, callback, *args, **kwargs):
        """A diagnostic mapping failure must never replace native execution or errors."""
        try:
            return callback(*args, **kwargs)
        except Exception as error:
            try:
                self.store_error(error)
            except Exception:
                pass  # Preserve the original operation even if diagnostics fail too.

    def _wrapper(self, name, binding, target, original, signature):
        mode = target.name.removeprefix("loading.")
        observer = self
        def wrapped(owner, *args, **kwargs):
            if observer.closed or observer.groups[name]["state"] != "active":
                return original(owner, *args, **kwargs)
            if mode in ("load", "construct"):
                attempt = observer._states.get(owner, observer.readiness.loading_attempt())
                row = observer._observe_safely(observer.activity._begin, "save_loading", owner) if mode == "load" or attempt is not None else None
                finished = False
                try:
                    result = original(owner, *args, **kwargs)
                except Exception as error:
                    finished = True
                    if row is not None:
                        observer._observe_safely(observer.activity._store_failure, error, row)
                    else:
                        observer._observe_safely(observer.activity.store_exception, error)
                    raise
                else:
                    finished = observer._observe_safely(observer._state_completed, mode, owner, attempt, row, result)
                    return result
                finally:
                    if row is not None:
                        observer._observe_safely(observer.activity._end, row)
                    if finished:
                        observer._observe_safely(observer._finish_attempt, attempt)
            attempt = None
            if mode in ("continue", "new", "selected", "team"):
                attempt = observer._observe_safely(observer.readiness.begin_loading, owner)
            try:
                result = original(owner, *args, **kwargs)
            except Exception as error:
                if attempt is not None and not hasattr(error, "launcher_activity"):
                    error.launcher_activity = observer.readiness.current_activity()
                    observer._observe_safely(observer.activity.store_exception, error)
                raise
            finally:
                if attempt is not None and not (getattr(owner, "is_loading", False) or getattr(owner, "_season_loading", False)):
                    observer._observe_safely(observer.readiness.end_loading, attempt)
            observer._observe_safely(observer._completed, mode, owner, args, kwargs)
            return result
        update_wrapper(wrapped, original)
        # Other trusted loading/save adapters may decorate this observer later.
        wrapped._tpm_activity_group = name
        return wrapped

    def _state_completed(self, mode, owner, attempt, row, result):
        if mode == "construct" and attempt is not None:
            self._states[owner] = attempt
        if mode == "load" and result is False:
            detail = str(getattr(owner, "last_load_error", None) or "The native save load did not succeed")
            record = build_log_record("ERROR", "Native save load failed", component="activity")
            # The native loader retained text, not an exception. Keep it whole
            # without inventing stack frames or consulting source files.
            record["traceback"] = "".join(c if c.isprintable() or c in "\n\t" else " " for c in detail)
            if row is not None:
                record["activity"] = self.activity._context(row)
            self.activity.submit_log(record)
            return True
        return False

    def _finish_attempt(self, attempt):
        if attempt is not None:
            if threading.current_thread() is threading.main_thread():
                self.readiness.end_loading(attempt)
            elif self.events is not None:
                self.events.loading_finished(attempt)

    def _completed(self, mode, owner, args, kwargs):
        if mode == "job":
            manager = args[0] if args else kwargs["manager"]
            screen = getattr(manager, "current", None)
            if not owner.done and not owner.cancelled and screen is not None:
                attempt = self.readiness.begin_loading(screen, replace=False)
                self._jobs[owner] = attempt
        elif mode in ("progress", "cancel") and (owner.done or owner.cancelled):
            attempt = self._jobs.pop(owner, None)
            if attempt is not None:
                self.readiness.end_loading(attempt)
