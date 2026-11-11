# SPDX-License-Identifier: MPL-2.0
"""Keep replies and wake-ups with the one admitted bridge request."""

from dataclasses import dataclass, field
import queue


@dataclass
class PendingRequest:
    """Correlate callbacks without allowing concurrent native Python entry."""

    token: str
    replies: queue.Queue = field(default_factory=queue.Queue)
