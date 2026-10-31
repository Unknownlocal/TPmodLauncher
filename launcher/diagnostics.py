# SPDX-License-Identifier: MPL-2.0
"""Write runtime diagnostics through one standard console/file configuration."""

from contextlib import contextmanager
from functools import partial
import logging

from hook_manager.diagnostics import CONTEXT, LEVELS, build_log_record, single_line


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
    message = record["detail"]
    if type(value) is dict and "traceback" in value:
        record["traceback"] = single_line(value["traceback"], 2048)
    if "traceback" in record:
        message += " traceback=" + record["traceback"]
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
def reporter(path):
    """Own this launch's two handlers without changing third-party loggers."""
    path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("modLauncher")
    previous = logger.level, logger.propagate
    logger.setLevel(logging.INFO)
    logger.propagate = False
    handlers = []
    try:
        for factory in (logging.StreamHandler, lambda: logging.FileHandler(path, mode="w", encoding="utf-8")):
            handler = factory()
            handlers.append(handler)
            handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
            logger.addHandler(handler)
        yield logger
    finally:
        for handler in handlers:
            logger.removeHandler(handler)
            handler.close()
        logger.setLevel(previous[0])
        logger.propagate = previous[1]
