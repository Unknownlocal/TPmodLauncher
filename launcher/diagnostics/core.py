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
    from hook_manager.diagnostics import CONTEXT, LEVELS, build_log_record, error_text, format_log_record
    from .log_output import LogOutput
    from .log_ownership import LogOwnership
    from .activity_tracker import ActivityTracker
    from .launch_context import LaunchContext
    from .log_port import LogPort
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
    _records.ExceptionSnapshot = _load_source(_folder.parent.parent / "hook_manager/exception_snapshot.py").ExceptionSnapshot
    build_log_record, error_text = _records.build_log_record, _records.error_text
    format_log_record = _records.format_log_record
    LogOutput = _load_source(_folder / "log_output.py").LogOutput
    LogOwnership = _load_source(_folder / "log_ownership.py").LogOwnership
    ActivityTracker = _load_source(_folder / "activity_tracker.py").ActivityTracker
    LaunchContext = _load_source(_folder / "launch_context.py").LaunchContext
    LogPort = _load_source(_folder / "log_port.py").LogPort


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
    port = bind_reporter(sink)
    context = {**port.fields, **context}
    context = {key: value for key, value in context.items() if key in ("pid", "mod", "owners", "folder")}
    tracker = port.owner.activity if port.owner is not None else None
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
    """Claim a launch; pass its lease explicitly to writers and presentation."""
    try:
        ownership = LogOwnership(path)
    except OSError as error:
        raise error_type("Another controller is running, or the controller lock is unavailable.") from error
    try:
        yield ownership
    finally:
        ownership.release()


def _build_logging_failure(outcome):
    """Describe file failure only after the primary operation has had its say."""
    return {"reason": "Launcher log could not be completed", "stage": "logging", "level": "ERROR",
            "activity": deepcopy(outcome.get("log_activity"))}


def sync_log(logger, *, timeout=1):
    """Await only the owned file queue, within a bounded failure/shutdown wait."""
    owner = bind_reporter(logger).owner
    return owner.output.sync_file(timeout) if owner is not None and owner.output is not None else False


def exception_from_record(message, record, exception_type=RuntimeError):
    """Keep complete plain diagnostics with the error, without formatting them.

    Select diagnostic fields rather than retaining unrelated UI or game data.
    The normal exception snapshot carries them through later cleanup and logging.
    """
    error = exception_type(message)
    pending, seen, records = [record], set(), []
    while pending:
        value = pending.pop()
        if type(value) is dict:
            if any(key in value for key in ("detail", "traceback", "exception_snapshot")):
                diagnostic = {key: value[key] for key in (*CONTEXT, "level", "detail", "traceback", "exception_snapshot", "activity")
                              if key in value}
                identity = json.dumps(diagnostic, sort_keys=True)
                if identity not in seen:
                    records.append(deepcopy(diagnostic))
                    seen.add(identity)
            pending.extend(reversed([item for key, item in value.items() if key != "exception_snapshot" and type(item) in (dict, list)]))
        elif type(value) is list:
            pending.extend(reversed(value))
    error.launcher_records = records
    return error


def handle_error(sink, error, *, message="LAUNCHER ERROR", level="ERROR", stage="launcher", shutdown=None, outcome=None, **context):
    """Write a complete failure and, at a fatal boundary, request owned game exit.

    Callers still release their own resources and choose their exit code. Logging
    ordinary ERROR records alone never closes the game.
    """
    port = bind_reporter(sink)
    owner = port.owner
    if outcome is None and owner is not None:
        outcome = owner.outcome
    if outcome is not None and outcome["failure"] is None:
        # Store plain values only. Keeping an exception would retain its frames
        # and game objects, and cleanup must not replace the original failure.
        tracker = owner.activity if owner is not None else None
        activity = getattr(error, "launcher_activity", None) or (tracker.snapshot() if tracker is not None else
                    {"kind": "launcher_startup", "description": "while starting the launcher"})
        outcome["failure"] = {"reason": str(error), "stage": stage, "level": level, "activity": deepcopy(activity)}
    try:
        port(message + ": " + str(error), level=level, error=error, fatal=True, **context)
    finally:
        # Even a failed output destination must not leave the owned game running.
        try:
            if shutdown is not None:
                shutdown.request()
        finally:
            sync_log(port)


def bind_reporter(sink, **context):
    """Adapt public callbacks once; bound ports retain the same launch owner.

    bind_log is a small trusted protocol because early startup loads these
    standard-library sources before normal package imports create another class.
    """
    bind = getattr(sink, "bind_log", None)
    if callable(bind):
        return bind(**context)
    if isinstance(sink, partial):
        if sink.func is write_log:
            return bind_reporter(sink.args[0], **{**sink.keywords, **context})
        bind = getattr(sink.func, "bind_log", None)
        if not sink.args and callable(bind):
            return bind(**{**sink.keywords, **context})
    return _make_reporter(sink, fields=context)


def _make_reporter(sink, *, owner=None, fields=None):
    # Select the destination here, never on every record submission.
    if isinstance(sink, logging.Logger):
        def write(record, message, fatal):
            level = LEVELS[record["level"]]
            name = sink.name + "." + record.get("component", "runtime")
            extra = {"launcher_fatal": fatal, "launcher_protected": "traceback" in record}
            if owner is not None:
                # A private logger keeps in-flight formatting tied to this launch,
                # even if cleanup finishes and another launch starts meanwhile.
                sink.handle(sink.makeRecord(name, level, "", 0, message, (), None, extra=extra))
            else:
                sink.getChild(record.get("component", "runtime")).log(level, "%s", message, extra=extra)
    else:
        def write(record, message, fatal):
            sink(message)
    return LogPort(lambda value, **options: _write_log(write, owner, value, **options), owner=owner, fields=fields)


def write_log(sink, value, *, level=_UNSET, error=_UNSET, fatal=_UNSET, **context):
    """Keep the public text/record facade; internal components retain a LogPort."""
    for name, option in (("level", level), ("error", error), ("fatal", fatal)):
        if option is not _UNSET:
            context[name] = option
    return bind_reporter(sink)(value, **context)


def _write_log(write, owner, value, *, level="INFO", error=None, fatal=False, **context):
    """Build one canonical record and project it through the adapted writer."""
    if owner is not None and owner.closed:
        return
    if type(value) is dict:
        level = value.get("level", level)
        context.update((key, value[key]) for key in CONTEXT if key in value)
        detail = value.get("detail", "")
    else:
        detail = value
    if type(level) is not str or level not in LEVELS:
        level, detail = "WARNING", f"Invalid diagnostic severity; {detail}"
    output = owner.output if owner is not None else None
    if output is not None and LEVELS[level] < output.minimum and not fatal:
        return  # Do not format errors or copy activity for suppressed routine logs.
    record = build_log_record(level, detail, error=error, **context)
    if type(value) is dict and "exception_snapshot" in value:
        record["exception_snapshot"] = value["exception_snapshot"]
    record = format_log_record(record)
    tracker = owner.activity if owner is not None else None
    supplied = value.get("activity") if type(value) is dict else None
    activity = getattr(error, "launcher_activity", None) or supplied or (tracker.snapshot(context.get("pid")) if tracker is not None else None)
    message = record["detail"]
    if activity is not None:
        message += " activity=" + json.dumps(activity, ensure_ascii=True)
    if type(value) is dict and "traceback" in value:
        record["traceback"] = error_text(value["traceback"])
    if "traceback" in record:
        message += "\n" + record["traceback"]
    # Component logger names and other context stay visible in
    # both destinations, including callbacks that do not consume LogRecords.
    fields = " ".join(f"{key}={record[key]}" for key in CONTEXT if key not in ("component",) and key in record)
    if fields:
        message = fields + " " + message
    write(record, message, fatal)


@contextmanager
def reporter(path, *, outcome=None, log_level="INFO", ownership=None):
    """Own independent console/file writers and certify only completed file I/O.

    Callers claim log_ownership before opening a log. Shutdown has one shared
    deadline; an unresponsive writer retains its own resources and lock lease
    until it finishes or the process exits, without blocking another destination.
    """
    minimum_level = parse_log_level(log_level)
    logger = logging.Logger("modLauncher", logging.DEBUG)
    if outcome is None:
        outcome = {}
    if not outcome:
        outcome.update(build_launch_outcome(path))
    owner = LaunchContext(logger, outcome, ActivityTracker(lambda message: owner.info("%s", message)), ownership)
    owner.reporter = _make_reporter(logger, owner=owner)
    logger.propagate = False
    output = None
    try:
        output = LogOutput(path, outcome, minimum=minimum_level, console=sys.stderr,
                           ownership=ownership,
                           activity=owner.activity.snapshot)
        owner.output = output
        logger.addHandler(output)
        try:
            yield owner
        finally:
            owner.closed = True
            deadline = time.monotonic() + 1
            try:
                output.sync_file(max(0, deadline - time.monotonic()))
                if outcome["failure"] is None and outcome["log_error"] is not None:
                    outcome["failure"] = _build_logging_failure(outcome)
                if outcome["failure"] is not None:
                    details = {key: value for key, value in outcome.items() if key not in ("log_written", "log_error")}
                    logger.error("LAUNCH FAILURE DETAILS %s", json.dumps(details, ensure_ascii=True), extra={"launcher_fatal": True})
            finally:
                logger.disabled = True
                logger.removeHandler(output)
                output.finish(deadline=deadline)
                if outcome["failure"] is None and outcome["log_error"] is not None:
                    outcome["failure"] = _build_logging_failure(outcome)
    finally:
        logger.disabled = True


def build_launch_outcome(path):
    """Describe this launch as plain values, including a log not yet opened.

    runtime_platform and log_path describe the controller's filesystem. A later
    native Linux supervisor can supply host context without guessing Wine paths.
    """
    return dict(session_id=uuid.uuid4().hex, runtime_platform=sys.platform,
                log_path=str(path.absolute()), log_written=False, log_error=None,
                failure=None, cleanup="not_started", game_close="not_requested")
