# SPDX-License-Identifier: MPL-2.0
"""Small log records that can cross the game/launcher boundary as plain data."""

from collections import deque

LEVELS = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}
CONTEXT = ("component", "pid", "scope", "operation", "mod")


def single_line(value, limit=4096):
    """Bound text and remove control characters before it reaches a log handler."""
    text = str(value)
    suffix = " [truncated]" if len(text) > limit else ""
    text = text[:limit - len(suffix)]
    return " ".join("".join(c if c.isprintable() else " " for c in text).split()) + suffix


def build_log_record(level, detail, *, error=None, **context):
    """Create bounded transport data; severity never decides operation outcomes."""
    record = {"level": level, "detail": single_line(detail)}
    record.update((key, single_line(context[key], 128)) for key in CONTEXT if key in context)
    if isinstance(error, BaseException):
        # Keep stack locations and the cause, without capturing local variables.
        # Source lines may belong to injected modules without a filesystem loader.
        import traceback
        frames = deque(traceback.walk_tb(error.__traceback__), maxlen=4)
        locations = " | ".join(f"{frame.f_code.co_filename}:{line} in {frame.f_code.co_name}" for frame, line in frames)
        cause = "".join(traceback.format_exception(error, limit=0))
        record["traceback"] = single_line((locations + ": " + cause)[-2048:], 2048)
    return record
