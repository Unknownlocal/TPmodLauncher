# SPDX-License-Identifier: MPL-2.0
"""Write runtime diagnostics through one standard console/file configuration."""

from contextlib import contextmanager, nullcontext
from copy import deepcopy
from functools import partial
import importlib.util
import json
import logging
from pathlib import Path
import sys
import time
import uuid

# Omitted options must not overwrite defaults held by a bound logging callback.
_UNSET = object()

if __package__:
    from hook_manager.diagnostics import CONTEXT, LEVELS, build_log_record, error_text
    from .log_output import LogOutput
    from .log_ownership import LogOwnership
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
    LogOutput = _load_source(_folder / "log_output.py").LogOutput
    LogOwnership = _load_source(_folder / "log_ownership.py").LogOwnership
    ActivityTracker = _load_source(_folder / "activity_tracker.py").ActivityTracker


def parse_log_level(value):
    """Resolve a 1-5 selector, logging name or standard integer threshold."""
    if type(value) is int:
        level = value
    elif isinstance(value, str):
        text = value.strip()
        level = logging.getLevelNamesMapping().get(text.upper())
        if level is None:
            try:
                level = int(text, 10)
            except ValueError:
                raise ValueError("Log level must be a logging name or non-negative integer (for example ERROR or 4)") from None
    else:
        raise ValueError("Log level must be a logging name or non-negative integer")
    if level < 0:
        raise ValueError("Log level must be non-negative")
    # Keep standard thresholds compatible; only the five short selectors expand.
    return level * 10 if 1 <= level <= 5 else level


def activity_scope(sink, kind, **context):
    """Use the logger's tracker without requiring text-only observers to own one."""
    if isinstance(sink, partial) and sink.func is write_log:
        context = {**sink.keywords, **context}
        sink = sink.args[0]
    context = {key: value for key, value in context.items() if key in ("pid", "mod", "owners", "folder")}
    tracker = getattr(sink, "launch_activity", None)
    return tracker.scope(kind, **context) if tracker is not None else nullcontext()


@contextmanager
def startup_step(sink, phase, **context):
    """Mark one startup operation, including failure, using elapsed real time.

    These boundaries run only during setup; they never sample gameplay. A start
    record remains useful if the operation hangs and cannot produce its end.
    """
    # Preserve severity/context when a controller supplies a bound write_log.
    submit_log = bind_reporter(sink, **context)
    submit_log("STARTUP " + json.dumps({"phase": phase, "state": "started"}), level="DEBUG")
    started, state = time.perf_counter(), "failed"
    try:
        yield
        state = "complete"
    finally:
        seconds = time.perf_counter() - started
        submit_log("STARTUP " + json.dumps({"phase": phase, "state": state, "seconds": round(seconds, 6)}),
                   level="DEBUG")


@contextmanager
def log_ownership(path, *, error_type=RuntimeError):
    """Claim a launch; stalled file workers retain the lock until they finish.

    The standard logger provides the shared early/runtime ownership slot, even
    when entry loads this standard-library module before package verification.
    """
    try:
        ownership = LogOwnership(path)
    except OSError as error:
        raise error_type("Another controller is running, or the controller lock is unavailable.") from error
    logger = logging.getLogger("modLauncher")
    previous = getattr(logger, "launch_ownership", None)
    logger.launch_ownership = ownership
    try:
        yield ownership
    finally:
        logger.launch_ownership = previous
        ownership.release()


def _build_logging_failure(outcome):
    """Describe file failure only after the primary operation has had its say."""
    return {"reason": "Launcher log could not be completed", "stage": "logging", "level": "ERROR",
            "activity": deepcopy(outcome.get("log_activity"))}


def sync_log(logger, *, timeout=1):
    """Await only the owned file queue, within a bounded failure/shutdown wait."""
    output = getattr(logger, "launch_output", None)
    return output.sync_file(timeout) if output is not None else False


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
        write_log(sink, message + ": " + str(error), level=level, error=error, fatal=True, **context)
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


def write_log(sink, value, *, level=_UNSET, error=_UNSET, fatal=_UNSET, **context):
    """Accept transport records or local text and keep callable observers usable.

    Standard Logger sinks retain severity. Plain text callbacks receive the same
    rendered message for display, without needing to understand log records.
    Fatal handling marks its records for retention regardless of the routine
    threshold; their severity is unchanged.
    """
    if isinstance(sink, partial) and sink.func is write_log:
        # A bound logger still accepts records and severity. Rendering it as a
        # plain text observer would lose the level and format context twice.
        options = dict(context)
        for name, option in (("level", level), ("error", error), ("fatal", fatal)):
            if option is not _UNSET:
                options[name] = option
        return sink(value, **options)
    level = "INFO" if level is _UNSET else level
    error = None if error is _UNSET else error
    fatal = False if fatal is _UNSET else fatal
    if type(value) is dict:
        level = value.get("level", level)
        context.update((key, value[key]) for key in CONTEXT if key in value)
        detail = value.get("detail", "")
    else:
        detail = value
    if type(level) is not str or level not in LEVELS:
        level, detail = "WARNING", f"Invalid diagnostic severity; {detail}"
    output = getattr(sink, "launch_output", None)
    if output is not None and LEVELS[level] < output.minimum and not fatal:
        return  # Do not format errors or copy activity for suppressed routine logs.
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
        sink.getChild(component).log(LEVELS[level], "%s", message,
                                     extra={"launcher_fatal": fatal, "launcher_protected": "traceback" in record})
    else:
        sink(message)


@contextmanager
def reporter(path, *, outcome=None, log_level="INFO"):
    """Own independent console/file writers and certify only completed file I/O.

    Callers claim log_ownership before opening a log. Shutdown has one shared
    deadline; an unresponsive writer retains its own resources and lock lease
    until it finishes or the process exits, without blocking another destination.
    """
    minimum_level = parse_log_level(log_level)
    logger = logging.getLogger("modLauncher")
    previous = logger.level, logger.propagate
    attributes = {name: getattr(logger, name, None) for name in ("launch_outcome", "launch_activity", "launch_output")}
    if outcome is None:
        outcome = {}
    if not outcome:
        outcome.update(build_launch_outcome(path))
    logger.launch_outcome = outcome
    logger.launch_activity = ActivityTracker(lambda message: logger.info("%s", message))
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    output = None
    try:
        output = LogOutput(path, outcome, minimum=minimum_level, console=sys.stderr,
                           ownership=getattr(logger, "launch_ownership", None),
                           activity=logger.launch_activity.snapshot)
        logger.launch_output = output
        logger.addHandler(output)
        try:
            yield logger
        finally:
            deadline = time.monotonic() + 1
            try:
                output.sync_file(max(0, deadline - time.monotonic()))
                if outcome["failure"] is None and outcome["log_error"] is not None:
                    outcome["failure"] = _build_logging_failure(outcome)
                if outcome["failure"] is not None:
                    details = {key: value for key, value in outcome.items() if key not in ("log_written", "log_error")}
                    logger.error("LAUNCH FAILURE DETAILS %s", json.dumps(details, ensure_ascii=True), extra={"launcher_fatal": True})
            finally:
                logger.removeHandler(output)
                output.finish(deadline=deadline)
                if outcome["failure"] is None and outcome["log_error"] is not None:
                    outcome["failure"] = _build_logging_failure(outcome)
    finally:
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
        for name, value in attributes.items():
            if value is None:
                if hasattr(logger, name):
                    delattr(logger, name)
            else:
                setattr(logger, name, value)


def build_launch_outcome(path):
    """Describe this launch as plain values, including a log not yet opened.

    runtime_platform and log_path describe the controller's filesystem. A later
    native Linux supervisor can supply host context without guessing Wine paths.
    """
    return dict(session_id=uuid.uuid4().hex, runtime_platform=sys.platform,
                log_path=str(path.absolute()), log_written=False, log_error=None,
                failure=None, cleanup="not_started", game_close="not_requested")
