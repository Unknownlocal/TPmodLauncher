# SPDX-License-Identifier: MPL-2.0
"""Keep unavailable validation from being mistaken for a rejected candidate."""

from contextlib import contextmanager, nullcontext
import threading

from ...manager import HookError
from ...diagnostics import build_log_record
from .cancellation import PersonnelCancellation


_local = threading.local()


def report_failure(guards, error):
    """Preserve error severity and report a failed native operation once."""
    operation = getattr(_local, "operation", None)
    if operation is not None and operation["reported"]:
        return
    if guards.submit_log is not None:
        guards.submit_log(build_log_record("ERROR", f"REGULATIONS VALIDATION FAILED: {error}",
                                        error=error, component="regulations", scope=guards.scope,
                                        **({"operation": operation["label"]} if operation else {})))
        if operation is not None:
            operation["reported"] = True


def stop_if_unavailable(outcome):
    """Latch failure even if a native helper catches the cancellation."""
    operation = getattr(_local, "operation", None)
    if operation is not None and outcome.status == "unavailable":
        operation["failure"] = outcome
        raise PersonnelCancellation(outcome)


def native_initiator():
    return "system" if getattr(_local, "operation", None) is not None else "unknown"


def operation_failure():
    operation = getattr(_local, "operation", None)
    return operation["failure"] if operation is not None else None


@contextmanager
def native_operation(guards, label):
    """Admit a fixed caller and deliver its failure to the native worker owner.

    Nested callers share failure state, but each adapter keeps its own worker
    admission. Native exception handlers can still perform their normal cleanup.
    If native code catches the failure, raise it again before reporting success.
    """
    previous = getattr(_local, "operation", None)
    operation = previous or {"label": label, "failure": None, "reported": False}
    _local.operation = operation
    # Keep the adapters in place throughout main-thread and worker operations,
    # including gaps between individual signings.
    scoped = not getattr(guards._local, "operation", False)
    try:
        with guards.operation() if scoped else nullcontext():
            if operation["failure"] is not None:
                raise PersonnelCancellation(operation["failure"])
            guards._require_available()
            yield
            if operation["failure"] is not None:
                raise PersonnelCancellation(operation["failure"])
    except PersonnelCancellation as error:
        if previous is not None or error.outcome.status != "unavailable":
            raise
        report_failure(guards, error)
        raise HookError(f"{label} stopped: {error}") from error
    finally:
        _local.operation = previous
