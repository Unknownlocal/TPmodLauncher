# SPDX-License-Identifier: MPL-2.0
"""Plain log records shared by the game adapters and launcher."""

if __package__:
    from .exception_snapshot import ExceptionSnapshot

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
CONTEXT = ("component", "pid", "scope", "operation", "mod")
# The injected host supplies a cached plain-data reader. Workers never inspect
# live game objects merely to construct a diagnostic record.
activity_provider = None


def single_line(value, limit=4096):
    """Bound text and remove control characters before it reaches a log handler."""
    text = str(value)
    suffix = " [truncated]" if len(text) > limit else ""
    text = text[:limit - len(suffix)]
    return " ".join("".join(c if c.isprintable() else " " for c in text).split()) + suffix


def error_text(error):
    """Keep complete exception chains and notes, without capturing local values.

    Injected game modules may have no source file. Python still supplies their
    filenames, line numbers and function names. Preserve line breaks for readable
    stacks, but remove terminal control characters from exception messages.
    """
    import traceback

    if isinstance(error, BaseException):
        # Python normally abbreviates wide/deep exception groups. Every member
        # can explain a different failed mod, so retain them all here.
        value = "".join(traceback.TracebackException.from_exception(
            error, max_group_width=float("inf"), max_group_depth=float("inf")).format())
    else:
        value = str(error)
    return "".join(c if c.isprintable() or c in "\n\t" else " " for c in value)


def build_log_record(level, detail, *, error=None, **context):
    """Capture complete errors as plain snapshots, without source lookup or I/O.

    Snapshots remain safe to retain in status records and serialise in replies.
    Only the journal worker or launcher renders them; no live frames are retained.
    """
    record = {"level": level, "detail": single_line(detail)}
    record.update((key, single_line(context[key], 128)) for key in CONTEXT if key in context)
    if error is not None and hasattr(error, "launcher_activity"):
        record["activity"] = dict(error.launcher_activity)
    elif activity_provider is not None:
        record["activity"] = activity_provider()
    if isinstance(error, BaseException):
        record["exception_snapshot"] = ExceptionSnapshot(error).to_data()
    elif error is not None:
        record["traceback"] = error_text(error)
    return record


def format_log_record(record, *, lookup_lines=True):
    """Render a captured error on the journal worker or the external launcher.

    Return a copy: the native owner may still hold the original status record.
    Failed source lookup retains every captured value instead of losing its cause.
    """
    if "exception_snapshot" not in record:
        return record
    import json
    value = dict(record)
    data = value.pop("exception_snapshot")
    try:
        failure = ExceptionSnapshot.from_data(data)
        value["traceback"] = error_text("".join(failure.format(lookup_lines=lookup_lines)))
    except Exception as error:
        value["traceback"] = ("Deferred error formatting failed: " + str(error) + "\n"
                              + json.dumps(data, ensure_ascii=True))
    return value
