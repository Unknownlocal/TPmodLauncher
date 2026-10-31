# SPDX-License-Identifier: MPL-2.0
"""One correlated transport request transferred to the owning controller thread."""

from dataclasses import dataclass, field
import threading


@dataclass
class TransportRequest:
    message: dict
    done: threading.Event = field(default_factory=threading.Event)
    response: dict | None = None
