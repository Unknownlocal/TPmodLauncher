# SPDX-License-Identifier: MPL-2.0
"""Write runtime diagnostics through one standard console/file configuration."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from functools import partial
import importlib.util
import json
import logging
import os
from pathlib import Path
import sys
import uuid

if __package__:
    from hook_manager.diagnostics import CONTEXT, LEVELS, build_log_record, error_text
    from .log_file_handler import LogFileHandler
    from .activity_tracker import ActivityTracker
else:
    # Entry needs only these standard-library sources before dependency/package
    # setup. Fixed paths avoid adding the game or an unverified package to sys.path.
    def _load_source(path):
        spec = importlib.util.spec_from_file_location("_launcher_logging_" + path.stem, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    _folder = Path(__file__).resolve().parent
    _records = _load_source(_folder.parent.parent / "hook_manager/diagnostics.py")
    CONTEXT, LEVELS = _records.CONTEXT, _records.LEVELS
    build_log_record, error_text = _records.build_log_record, _records.error_text
    LogFileHandler = _load_source(_folder / "log_file_handler.py").LogFileHandler
    ActivityTracker = _load_source(_folder / "activity_tracker.py").ActivityTracker


def activity_scope(sink, kind, **context):
    """Use the logger's tracker without requiring text-only observers to own one."""
    if isinstance(sink, partial) and sink.func is write_log:
        context = {**sink.keywords, **context}
        sink = sink.args[0]
    context = {key: value for key, value in context.items() if key in ("pid", "mod", "owners", "folder")}
    tracker = getattr(sink, "launch_activity", None)
    return tracker.scope(kind, **context) if tracker is not None else nullcontext()


@contextmanager
def log_ownership(path, *, error_type=RuntimeError):
    """Claim this launch before opening its log, including dependency checks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        # Windows locks a byte range. Keep the inode/file after release so another
        # opener cannot acquire a different replacement while this one is locked.
        if stream.seek(0, os.SEEK_END) == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise error_type("Another controller is running, or the controller lock is unavailable.") from error
        yield


def _store_log_error(logger, error):
    """Keep the first file failure and print it without re-entering logging."""
    outcome = logger.launch_outcome
    outcome["log_written"] = False
    if outcome["log_error"] is None:
        outcome["log_error"] = error_text(error)
        tracker = getattr(logger, "launch_activity", None)
        outcome["log_activity"] = tracker.snapshot() if tracker is not None else None
        try:
            print("Launcher log could not be completed: " + str(outcome["log_path"])
                  + "\n" + outcome["log_error"], file=sys.stderr, flush=True)
        except (OSError, ValueError):
            pass  # A closed console cannot report this; retain the write result.


def _build_logging_failure(outcome):
    """Describe file failure only after the primary operation has had its say."""
    return {"reason": "Launcher log could not be completed", "stage": "logging", "level": "ERROR",
            "activity": deepcopy(outcome.get("log_activity"))}


def sync_log(logger):
    """Flush and synchronise owned file handlers; retain failures for the caller."""
    if not isinstance(logger, logging.Logger) or not hasattr(logger, "launch_outcome"):
        return
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            try:
                handler.flush()
                os.fsync(handler.stream.fileno())
                logger.launch_outcome["log_written"] = logger.launch_outcome["log_error"] is None
            except (OSError, ValueError) as error:
                _store_log_error(logger, error)


def exception_from_record(message, record, exception_type=RuntimeError):
    """Keep diagnostics from a reply, including nested status and notice records.

    Only diagnostic text becomes notes; other response data stays out of logs.
    This must happen before unwinding can discard separately queued messages.
    """
    error = exception_type(message)
    pending, seen = [record], set()
    while pending:
        value = pending.pop()
        if type(value) is dict:
            detail = value.get("traceback") or value.get("detail")
            if detail:
                text = error_text(detail)
                if text not in seen:
                    error.add_note(text)
                    seen.add(text)
            pending.extend(reversed([item for item in value.values() if type(item) in (dict, list)]))
        elif type(value) is list:
            pending.extend(reversed(value))
    return error


def handle_error(sink, error, *, message="LAUNCHER ERROR", level="ERROR", stage="launcher", shutdown=None, outcome=None, **context):
    """Write a complete failure and, at a fatal boundary, request owned game exit.

    Callers still release their own resources and choose their exit code. Logging
    ordinary ERROR records alone never closes the game.
    """
    if outcome is None:
        outcome = getattr(sink, "launch_outcome", None)
    if outcome is not None and outcome["failure"] is None:
        # Store plain values only. Keeping an exception would retain its frames
        # and game objects, and cleanup must not replace the original failure.
        tracker = getattr(sink, "launch_activity", None)
        activity = getattr(error, "launcher_activity", None) or (tracker.snapshot() if tracker is not None else
                    {"kind": "launcher_startup", "description": "while starting the launcher"})
        outcome["failure"] = {"reason": str(error), "stage": stage, "level": level, "activity": deepcopy(activity)}
    try:
        write_log(sink, message + ": " + str(error), level=level, error=error, **context)
    finally:
        # Even a failed output destination must not leave the owned game running.
        try:
            if shutdown is not None:
                shutdown.request()
        finally:
            sync_log(sink)


def bind_reporter(sink, **context):
    """Add context without formatting away severity in nested logging callbacks."""
    if isinstance(sink, partial) and sink.func is write_log:
        context = {**sink.keywords, **context}
        sink = sink.args[0]
    return partial(write_log, sink, **context)


def write_log(sink, value, *, level="INFO", error=None, **context):
    """Accept transport records or local text and keep callable observers usable.

    Standard Logger sinks retain severity. Plain text callbacks receive the same
    rendered message for display, without needing to understand log records.
    """
    if type(value) is dict:
        level = value.get("level", level)
        context.update((key, value[key]) for key in CONTEXT if key in value)
        detail = value.get("detail", "")
    else:
        detail = value
    if type(level) is not str or level not in LEVELS:
        level, detail = "WARNING", f"Invalid diagnostic severity; {detail}"
    record = build_log_record(level, detail, error=error, **context)
    tracker = getattr(sink, "launch_activity", None)
    supplied = value.get("activity") if type(value) is dict else None
    activity = getattr(error, "launcher_activity", None) or supplied or (tracker.snapshot(context.get("pid")) if tracker is not None else None)
    message = record["detail"]
    if activity is not None:
        message += " activity=" + json.dumps(activity, ensure_ascii=True)
    if type(value) is dict and "traceback" in value:
        record["traceback"] = error_text(value["traceback"])
    if "traceback" in record:
        message += "\n" + record["traceback"]
    # Components use standard child loggers. Other context stays visible in
    # both destinations, including callbacks that do not consume LogRecords.
    fields = " ".join(f"{key}={record[key]}" for key in CONTEXT if key not in ("component",) and key in record)
    if fields:
        message = fields + " " + message
    if isinstance(sink, logging.Logger):
        component = record.get("component", "runtime")
        sink.getChild(component).log(LEVELS[level], "%s", message)
    else:
        sink(message)


@contextmanager
def reporter(path, *, outcome=None):
    """Own early/runtime logging and finish a plain failure outcome without UI.

    Callers acquire log_ownership first. File failure leaves console logging
    usable; outcome records whether the complete log was actually synchronised.
    """
    logger = logging.getLogger("modLauncher")
    previous = logger.level, logger.propagate
    previous_outcome = getattr(logger, "launch_outcome", None)
    previous_activity = getattr(logger, "launch_activity", None)
    if outcome is None:
        outcome = {}
    if not outcome:
        outcome.update(build_launch_outcome(path))
    logger.launch_outcome = outcome
    logger.launch_activity = ActivityTracker(lambda message: logger.info("%s", message))
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = []
    try:
        console = logging.StreamHandler()
        handlers.append(console)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handlers.append(LogFileHandler(path, partial(_store_log_error, logger)))
        except OSError as error:
            _store_log_error(logger, error)
        for handler in handlers:
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(handler)
        yield logger
    finally:
        try:
            sync_log(logger)
            if outcome["failure"] is None and outcome["log_error"] is not None:
                outcome["failure"] = _build_logging_failure(outcome)
            if outcome["failure"] is not None:
                # A file cannot certify its own final sync/close. Persist failure
                # and cleanup details; only the returned outcome records the final
                # write result, after every file operation has completed.
                details = {key: value for key, value in outcome.items() if key not in ("log_written", "log_error")}
                logger.error("LAUNCH FAILURE DETAILS %s", json.dumps(details, ensure_ascii=True))
                sync_log(logger)
        finally:
            for handler in handlers:
                logger.removeHandler(handler)
                try:
                    handler.close()
                except (OSError, ValueError) as error:
                    _store_log_error(logger, error)
            if outcome["failure"] is None and outcome["log_error"] is not None:
                outcome["failure"] = _build_logging_failure(outcome)
            logger.setLevel(previous[0])
            logger.propagate = previous[1]
            if previous_outcome is None:
                del logger.launch_outcome
            else:
                logger.launch_outcome = previous_outcome
            if previous_activity is None:
                del logger.launch_activity
            else:
                logger.launch_activity = previous_activity


def build_launch_outcome(path):
    """Describe this launch as plain values, including a log not yet opened.

    runtime_platform and log_path describe the controller's filesystem. A later
    native Linux supervisor can supply host context without guessing Wine paths.
    """
    return dict(session_id=uuid.uuid4().hex, runtime_platform=sys.platform,
                log_path=str(path.absolute()), log_written=False, log_error=None,
                failure=None, cleanup="not_started", game_close="not_requested")
