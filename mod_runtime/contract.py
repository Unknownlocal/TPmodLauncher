# SPDX-License-Identifier: MPL-2.0
"""Typed host interface of the trusted runner; permissions belong to each mod."""

from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class HostOperation:
    """An operation's required permission and scalar WIT signature."""

    capability: str
    parameters: tuple[str, ...]
    result: str | None = None


HOST_OPERATIONS = MappingProxyType({
    "report": HostOperation("diagnostics", ("string",)),
    "add-menu-button": HostOperation("main-menu", ("string", "string", "string")),
})
