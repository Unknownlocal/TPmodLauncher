# SPDX-License-Identifier: MPL-2.0
"""Plain log records shared by the game adapters and launcher."""

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
    """Bound routine summaries; keep supplied errors complete as plain text.

    Severity never decides operation outcomes. Exception text has no length or
    frame limit, so the original cause survives transport and file logging.
    """
    record = {"level": level, "detail": single_line(detail)}
    record.update((key, single_line(context[key], 128)) for key in CONTEXT if key in context)
    if error is not None and hasattr(error, "launcher_activity"):
        record["activity"] = dict(error.launcher_activity)
    elif activity_provider is not None:
        record["activity"] = activity_provider()
    if error is not None:
        record["traceback"] = error_text(error)
    return record
