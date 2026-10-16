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
    "register-settings": HostOperation("ui-settings", ("string",), "string"),
    "save-settings": HostOperation("ui-settings", ("string",), "string"),
    "register-theme": HostOperation("ui-theme", ("string",)),
})

# Source manifests select an exact public version, independently of the runner ABI.
SOURCE_CAPABILITIES = MappingProxyType({
    "0.2.0": frozenset({"diagnostics", "main-menu"}),
    "0.3.0": frozenset(spec.capability for spec in HOST_OPERATIONS.values()),
})
