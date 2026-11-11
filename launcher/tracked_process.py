# SPDX-License-Identifier: MPL-2.0
"""Keep one verified game's resources together through attachment and retirement."""

from dataclasses import dataclass, field

from .process_cancellation import ProcessCancellation


@dataclass(eq=False)
class TrackedProcess:
    """Identify an attachment by owner identity, even when the OS reuses its PID.

    The controller thread fills and releases these resources. Callbacks retain
    this owner and use its thread-safe cancellation, never a fresh PID lookup.
    """

    pid: int
    cancellation: ProcessCancellation = field(default_factory=ProcessCancellation)
    session: object = None
    exit_wait: object = None
    bridge: object = None
    mods: object = None

    def has_exited(self):
        """Check the pinned OS process rather than a later occupant of its PID."""
        return self.exit_wait is not None and self.exit_wait.has_exited()
