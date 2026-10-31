# SPDX-License-Identifier: MPL-2.0
"""Stop an admitted game action when its required validation cannot run."""

from contextlib import contextmanager, nullcontext
import threading

from ..manager import HookError
from ..diagnostics import build_log_record
from .validation_cancellation import ValidationCancellation


_local = threading.local()


def submit_validation_failure(guards, error):
    """Submit an ERROR record only once within the current operation."""
    operation = getattr(_local, "operation", None)
    if operation is not None and operation["failure_submitted"]:
        return
    if guards.submit_log is not None:
        guards.submit_log(build_log_record("ERROR", f"REGULATIONS VALIDATION FAILED: {error}",
                                        error=error, component="regulations", scope=guards.scope,
                                        **({"operation": operation["label"]} if operation else {})))
        if operation is not None:
            operation["failure_submitted"] = True


def stop_if_unavailable(outcome):
    """Retain failure even if a native helper catches the interruption."""
    operation = getattr(_local, "operation", None)
    if operation is not None and outcome.status == "unavailable":
        operation["failure"] = outcome
        raise ValidationCancellation(outcome)


def operation_initiator():
    """Mark an admitted operation as system work; otherwise leave its origin unknown."""
    return "system" if getattr(_local, "operation", None) is not None else "unknown"


def operation_failure():
    """Return the validation failure retained by the enclosing operation."""
    operation = getattr(_local, "operation", None)
    return operation["failure"] if operation is not None else None


@contextmanager
def validation_operation(guards, label):
    """Keep required validation available for an admitted game operation.

    Nested callers share failure state, but each adapter keeps its own worker
    admission. Native exception handlers can still perform their normal cleanup.
    If native code catches the failure, raise it again before returning success.
    """
    previous = getattr(_local, "operation", None)
    operation = previous or {"label": label, "failure": None, "failure_submitted": False}
    _local.operation = operation
    # Keep the adapters in place throughout main-thread and worker operations,
    # including gaps between individual validation calls.
    scoped = not getattr(guards._local, "operation", False)
    try:
        with guards.operation() if scoped else nullcontext():
            if operation["failure"] is not None:
                raise ValidationCancellation(operation["failure"])
            guards._require_available()
            yield
            if operation["failure"] is not None:
                raise ValidationCancellation(operation["failure"])
    except ValidationCancellation as error:
        if previous is not None or error.outcome.status != "unavailable":
            raise
        submit_validation_failure(guards, error)
        raise HookError(f"{label} stopped: {error}") from error
    finally:
        _local.operation = previous
